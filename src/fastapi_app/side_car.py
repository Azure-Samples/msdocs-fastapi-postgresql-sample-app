"""
side_car.py — Demo: MCP Server on Azure App Service with Agent ID SDK Token Validation
=======================================================================================

This file demonstrates how an MCP server hosted on Azure App Service can use
the Microsoft Entra SDK for AgentID (sidecar) for token validation instead of
(or in addition to) EasyAuth.

Architecture:
  ┌─────────────┐     ┌─────────────────────────┐     ┌──────────────────────┐
  │  Foundry     │────▶│  FastAPI + MCP Server    │────▶│  Agent ID SDK        │
  │  Agent       │     │  (this file)             │     │  Sidecar             │
  │              │◀────│  port 8000               │◀────│  http://localhost:5000│
  └─────────────┘     └─────────────────────────┘     └──────────────────────┘
                           │                               │
                           │  Forwards Authorization       │  Returns validated
                           │  header to /Validate           │  claims (oid, tid,
                           │                               │  roles, scopes)
                           ▼                               │
                      ┌──────────┐                         │
                      │ PostgreSQL│                         │
                      └──────────┘

Usage:
  1. Deploy the Agent ID SDK sidecar (see: https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/installation)
  2. Set environment variables:
       SIDECAR_URL=http://localhost:5000       (Agent ID SDK endpoint)
       AzureAd__TenantId=<your-tenant-id>
       AzureAd__ClientId=<your-api-client-id>
       AzureAd__Audience=api://<your-api-id>
  3. Run:  uvicorn fastapi_app.side_car:app --host 0.0.0.0 --port 8000

Reference:
  https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/scenarios/validate-authorization-header
"""

import json
import logging
import os
import pathlib
from datetime import datetime

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.sql import func
from sqlmodel import Session, select

from .models import Restaurant, Review, engine
from .mcp_server import mcp, mcp_lifespan

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("side_car")
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Agent ID SDK sidecar configuration
# ---------------------------------------------------------------------------
SIDECAR_URL = os.getenv("SIDECAR_URL", "http://localhost:5000")

# ---------------------------------------------------------------------------
# FastAPI app with MCP mounted
# ---------------------------------------------------------------------------
app = FastAPI(
    title="MCP Server with Agent ID SDK Token Validation",
    lifespan=mcp_lifespan,
)
app.mount("/mcp", mcp.streamable_http_app())
parent_path = pathlib.Path(__file__).parent.parent
app.mount("/mount", StaticFiles(directory=parent_path / "static"), name="static")
templates = Jinja2Templates(directory=parent_path / "templates")
templates.env.globals["prod"] = os.environ.get("RUNNING_IN_PRODUCTION", False)
templates.env.globals["url_for"] = app.url_path_for


# ---------------------------------------------------------------------------
# Agent ID SDK Token Validation Helper
# ---------------------------------------------------------------------------
async def validate_token_with_sidecar(authorization_header: str) -> dict:
    """
    Validate a bearer token by forwarding the Authorization header to the
    Microsoft Entra SDK for AgentID sidecar's /Validate endpoint.

    Returns the validated claims dict on success, raises HTTPException on failure.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{SIDECAR_URL}/Validate",
            headers={"Authorization": authorization_header},
        )

    if response.status_code == 401:
        logger.warning("Agent ID SDK: Token invalid or expired")
        raise HTTPException(status_code=401, detail="Token invalid or expired")
    if response.status_code == 403:
        logger.warning("Agent ID SDK: Token missing required scopes")
        raise HTTPException(status_code=403, detail="Insufficient scopes or roles")
    if not response.is_success:
        logger.error("Agent ID SDK: Unexpected error %s", response.status_code)
        raise HTTPException(status_code=401, detail="Token validation failed")

    return response.json()


def extract_user_info(validation: dict) -> dict:
    """Extract user/agent identity from the Agent ID SDK validation response."""
    claims = validation.get("claims", {})
    return {
        "id": claims.get("oid"),
        "upn": claims.get("upn"),
        "app_id": claims.get("appid") or claims.get("azp"),
        "tenant_id": claims.get("tid"),
        "scopes": claims.get("scp", "").split(" ") if claims.get("scp") else [],
        "roles": claims.get("roles", []),
        "audience": claims.get("aud"),
        "issuer": claims.get("iss"),
        "claims": claims,
    }


# ---------------------------------------------------------------------------
# Middleware: Validate tokens on /mcp/** routes via Agent ID SDK sidecar
# ---------------------------------------------------------------------------
@app.middleware("http")
async def agent_id_auth_middleware(request: Request, call_next):
    """
    Token validation middleware using the Microsoft Entra SDK for AgentID.

    - Requests to /mcp/** require a valid bearer token.
    - The token is validated by forwarding the Authorization header to the
      Agent ID SDK sidecar at SIDECAR_URL/Validate.
    - Validated claims are stored in request.state.user for downstream use.
    - Non-MCP routes (web UI) pass through without token validation.
    """
    if request.url.path.startswith("/mcp"):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.info("AUTH: Missing bearer token for %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=401,
                content={"error": "No authorization token provided"},
            )

        try:
            validation = await validate_token_with_sidecar(auth_header)
            user_info = extract_user_info(validation)
            request.state.user = user_info

            logger.info(
                "=== Agent ID SDK AUTH for %s %s ===",
                request.method,
                request.url.path,
            )
            logger.info("  OID:     %s", user_info["id"])
            logger.info("  AppID:   %s", user_info["app_id"])
            logger.info("  Tenant:  %s", user_info["tenant_id"])
            logger.info("  Roles:   %s", user_info["roles"])
            logger.info("  Scopes:  %s", user_info["scopes"])

        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": exc.detail},
            )
        except httpx.ConnectError:
            logger.error("Agent ID SDK sidecar unreachable at %s", SIDECAR_URL)
            return JSONResponse(
                status_code=503,
                content={"error": "Agent ID SDK sidecar unavailable"},
            )

    response = await call_next(request)
    return response


# ---------------------------------------------------------------------------
# Auth debug endpoint — inspect validated claims (demo only)
# ---------------------------------------------------------------------------
@app.get("/auth-debug", response_class=JSONResponse)
async def auth_debug(request: Request):
    """Debug endpoint: shows validated claims from the Agent ID SDK sidecar."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"error": "No bearer token provided"},
        )

    validation = await validate_token_with_sidecar(auth_header)
    user_info = extract_user_info(validation)
    return JSONResponse(content={
        "sidecar_url": SIDECAR_URL,
        "validation_response": validation,
        "extracted_user": user_info,
    })


# ---------------------------------------------------------------------------
# Role-based authorization helper (for MCP tool protection)
# ---------------------------------------------------------------------------
def require_role(request: Request, required_role: str):
    """Check that the validated token includes the required role."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if required_role not in user.get("roles", []):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{required_role}' required",
        )


# ---------------------------------------------------------------------------
# Web UI routes (no token validation — served to browsers)
# ---------------------------------------------------------------------------
def get_db_session():
    with Session(engine) as session:
        yield session


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session: Session = Depends(get_db_session)):
    logger.info("root called")
    statement = (
        select(
            Restaurant,
            func.avg(Review.rating).label("avg_rating"),
            func.count(Review.id).label("review_count"),
        )
        .outerjoin(Review, Review.restaurant == Restaurant.id)
        .group_by(Restaurant.id)
    )
    results = session.exec(statement).all()

    restaurants = []
    for restaurant, avg_rating, review_count in results:
        restaurant_dict = restaurant.dict()
        restaurant_dict["avg_rating"] = avg_rating
        restaurant_dict["review_count"] = review_count
        restaurant_dict["stars_percent"] = (
            round((float(avg_rating) / 5.0) * 100) if review_count > 0 else 0
        )
        restaurants.append(restaurant_dict)

    return templates.TemplateResponse("index.html", {"request": request, "restaurants": restaurants})


@app.get("/create", response_class=HTMLResponse)
async def create_restaurant(request: Request):
    return templates.TemplateResponse("create_restaurant.html", {"request": request})


@app.post("/add", response_class=RedirectResponse)
async def add_restaurant(
    request: Request,
    restaurant_name: str = Form(...),
    street_address: str = Form(...),
    description: str = Form(...),
    session: Session = Depends(get_db_session),
):
    restaurant = Restaurant()
    restaurant.name = restaurant_name
    restaurant.street_address = street_address
    restaurant.description = description
    session.add(restaurant)
    session.commit()
    session.refresh(restaurant)
    return RedirectResponse(
        url=app.url_path_for("details", id=restaurant.id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/details/{id}", response_class=HTMLResponse)
async def details(request: Request, id: int, session: Session = Depends(get_db_session)):
    restaurant = session.exec(select(Restaurant).where(Restaurant.id == id)).first()
    reviews = session.exec(select(Review).where(Review.restaurant == id)).all()
    review_count = len(reviews)
    avg_rating = (
        sum(r.rating for r in reviews if r.rating is not None) / review_count
        if review_count > 0
        else 0
    )
    restaurant_dict = restaurant.dict()
    restaurant_dict["avg_rating"] = avg_rating
    restaurant_dict["review_count"] = review_count
    restaurant_dict["stars_percent"] = (
        round((float(avg_rating) / 5.0) * 100) if review_count > 0 else 0
    )
    return templates.TemplateResponse(
        "details.html", {"request": request, "restaurant": restaurant_dict, "reviews": reviews}
    )


@app.post("/review/{id}", response_class=RedirectResponse)
async def add_review(
    request: Request,
    id: int,
    user_name: str = Form(...),
    rating: str = Form(...),
    review_text: str = Form(...),
    session: Session = Depends(get_db_session),
):
    review = Review()
    review.restaurant = id
    review.review_date = datetime.now()
    review.user_name = user_name
    review.rating = int(rating)
    review.review_text = review_text
    session.add(review)
    session.commit()
    return RedirectResponse(
        url=app.url_path_for("details", id=id),
        status_code=status.HTTP_303_SEE_OTHER,
    )
