import base64
import json
import logging
import os
import pathlib
from datetime import datetime

from azure.monitor.opentelemetry import configure_azure_monitor
from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.sql import func
from sqlmodel import Session, select

from .models import Restaurant, Review, engine

# Setup logger and Azure Monitor:
logger = logging.getLogger("app")
logger.setLevel(logging.INFO)
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor()


# Setup FastAPI app:
from .mcp_server import mcp, mcp_lifespan
app = FastAPI(lifespan=mcp_lifespan)
app.mount("/mcp", mcp.streamable_http_app())
parent_path = pathlib.Path(__file__).parent.parent
app.mount("/mount", StaticFiles(directory=parent_path / "static"), name="static")
templates = Jinja2Templates(directory=parent_path / "templates")
templates.env.globals["prod"] = os.environ.get("RUNNING_IN_PRODUCTION", False)
# Use relative path for url_for, so that it works behind a proxy like Codespaces
templates.env.globals["url_for"] = app.url_path_for


# --- Auth claim/permission logging helpers ---

def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verification (for logging/demo only)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        # Add padding
        payload += "=" * (4 - len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}


def _extract_auth_info(request: Request) -> dict:
    """Extract auth claims from EasyAuth headers or Authorization bearer token."""
    auth_info = {
        "authenticated": False,
        "auth_source": None,
        "claims": {},
        "permissions": [],
        "roles": [],
        "scopes": [],
    }

    # Check EasyAuth headers (App Service Authentication)
    principal_name = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME")
    principal_id = request.headers.get("X-MS-CLIENT-PRINCIPAL-ID")
    principal_idp = request.headers.get("X-MS-CLIENT-PRINCIPAL-IDP")
    client_principal = request.headers.get("X-MS-CLIENT-PRINCIPAL")

    if principal_name or principal_id:
        auth_info["authenticated"] = True
        auth_info["auth_source"] = "EasyAuth"
        auth_info["claims"]["principal_name"] = principal_name
        auth_info["claims"]["principal_id"] = principal_id
        auth_info["claims"]["identity_provider"] = principal_idp

        # Decode the full client principal (base64-encoded JSON)
        if client_principal:
            try:
                decoded = base64.b64decode(client_principal)
                principal_data = json.loads(decoded)
                auth_info["claims"]["full_principal"] = principal_data
                # Extract roles from claims
                for claim in principal_data.get("claims", []):
                    if claim.get("typ") == "roles":
                        auth_info["roles"].append(claim.get("val"))
                    elif claim.get("typ") == "scp":
                        auth_info["scopes"] = claim.get("val", "").split(" ")
                    elif claim.get("typ") == "permissions":
                        auth_info["permissions"].append(claim.get("val"))
            except Exception:
                pass

    # Check Authorization header (Bearer token)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        jwt_claims = _decode_jwt_payload(token)
        if jwt_claims:
            auth_info["authenticated"] = True
            auth_info["auth_source"] = auth_info.get("auth_source") or "Bearer"
            auth_info["claims"]["jwt"] = {
                "sub": jwt_claims.get("sub"),
                "aud": jwt_claims.get("aud"),
                "iss": jwt_claims.get("iss"),
                "appid": jwt_claims.get("appid") or jwt_claims.get("azp"),
                "oid": jwt_claims.get("oid"),
                "tid": jwt_claims.get("tid"),
                "name": jwt_claims.get("name"),
                "preferred_username": jwt_claims.get("preferred_username"),
                "exp": jwt_claims.get("exp"),
                "iat": jwt_claims.get("iat"),
            }
            auth_info["roles"] = jwt_claims.get("roles", [])
            auth_info["scopes"] = jwt_claims.get("scp", "").split(" ") if jwt_claims.get("scp") else []
            auth_info["permissions"] = jwt_claims.get("permissions", [])

    return auth_info


@app.middleware("http")
async def log_auth_claims(request: Request, call_next):
    """Middleware to log auth claims/permissions on every request for demo."""
    auth_info = _extract_auth_info(request)
    if auth_info["authenticated"]:
        logger.info("=== AUTH INFO for %s %s ===", request.method, request.url.path)
        logger.info("  Source: %s", auth_info["auth_source"])
        logger.info("  Roles: %s", auth_info["roles"])
        logger.info("  Scopes: %s", auth_info["scopes"])
        logger.info("  Permissions: %s", auth_info["permissions"])
        logger.info("  Claims: %s", json.dumps(auth_info["claims"], indent=2, default=str))
    else:
        logger.info("AUTH: No auth credentials for %s %s", request.method, request.url.path)
    response = await call_next(request)
    return response


@app.get("/auth-debug", response_class=JSONResponse)
async def auth_debug(request: Request):
    """Debug endpoint to inspect auth claims/permissions (for demo only)."""
    auth_info = _extract_auth_info(request)
    return JSONResponse(content={
        "path": str(request.url),
        "method": request.method,
        "auth": auth_info,
        "headers": {
            "X-MS-CLIENT-PRINCIPAL-NAME": request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME"),
            "X-MS-CLIENT-PRINCIPAL-ID": request.headers.get("X-MS-CLIENT-PRINCIPAL-ID"),
            "X-MS-CLIENT-PRINCIPAL-IDP": request.headers.get("X-MS-CLIENT-PRINCIPAL-IDP"),
            "Authorization": "Bearer <present>" if request.headers.get("Authorization") else None,
        },
    })


# Dependency to get the database session
def get_db_session():
    with Session(engine) as session:
        yield session


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session: Session = Depends(get_db_session)):
    logger.info("root called")
    statement = (
        select(Restaurant, func.avg(Review.rating).label("avg_rating"), func.count(Review.id).label("review_count"))
        .outerjoin(Review, Review.restaurant == Restaurant.id)
        .group_by(Restaurant.id)
    )
    results = session.exec(statement).all()

    restaurants = []
    for restaurant, avg_rating, review_count in results:
        restaurant_dict = restaurant.dict()
        restaurant_dict["avg_rating"] = avg_rating
        restaurant_dict["review_count"] = review_count
        restaurant_dict["stars_percent"] = round((float(avg_rating) / 5.0) * 100) if review_count > 0 else 0
        restaurants.append(restaurant_dict)

    return templates.TemplateResponse("index.html", {"request": request, "restaurants": restaurants})


@app.get("/create", response_class=HTMLResponse)
async def create_restaurant(request: Request):
    logger.info("Request for add restaurant page received")
    return templates.TemplateResponse("create_restaurant.html", {"request": request})


@app.post("/add", response_class=RedirectResponse)
async def add_restaurant(
    request: Request, restaurant_name: str = Form(...), street_address: str = Form(...), description: str = Form(...),
    session: Session = Depends(get_db_session)
):
    logger.info("name: %s address: %s description: %s", restaurant_name, street_address, description)
    restaurant = Restaurant()
    restaurant.name = restaurant_name
    restaurant.street_address = street_address
    restaurant.description = description
    session.add(restaurant)
    session.commit()
    session.refresh(restaurant)

    return RedirectResponse(url=app.url_path_for("details", id=restaurant.id), status_code=status.HTTP_303_SEE_OTHER)


@app.get("/details/{id}", response_class=HTMLResponse)
async def details(request: Request, id: int, session: Session = Depends(get_db_session)):
    restaurant = session.exec(select(Restaurant).where(Restaurant.id == id)).first()
    reviews = session.exec(select(Review).where(Review.restaurant == id)).all()

    review_count = len(reviews)

    avg_rating = 0
    if review_count > 0:
        avg_rating = sum(review.rating for review in reviews if review.rating is not None) / review_count

    restaurant_dict = restaurant.dict()
    restaurant_dict["avg_rating"] = avg_rating
    restaurant_dict["review_count"] = review_count
    restaurant_dict["stars_percent"] = round((float(avg_rating) / 5.0) * 100) if review_count > 0 else 0

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

    return RedirectResponse(url=app.url_path_for("details", id=id), status_code=status.HTTP_303_SEE_OTHER)
