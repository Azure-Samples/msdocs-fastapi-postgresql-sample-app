import logging
import os
import pathlib
from datetime import datetime

from azure.monitor.opentelemetry import configure_azure_monitor
from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.sql import func
from sqlmodel import Session, select

from .mcp_server import mcp, mcp_lifespan
from .models import Restaurant, Review, engine

# Setup logger and Azure Monitor:
logger = logging.getLogger("app")
logger.setLevel(logging.INFO)
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor()


# Setup FastAPI app:

parent_path = pathlib.Path(__file__).parent.parent
app = FastAPI(lifespan=mcp_lifespan)
app.mount("/api", mcp.streamable_http_app())
app.mount("/static", StaticFiles(directory=parent_path / "static"), name="static")

# Create Jinja2 environment with caching disabled to avoid issues
jinja_env = Environment(
    loader=FileSystemLoader(parent_path / "templates"),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,  # Disable caching to avoid 'unhashable type' error
)
jinja_env.globals["prod"] = bool(os.environ.get("RUNNING_IN_PRODUCTION", False))


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
        restaurant_dict = restaurant.model_dump()
        restaurant_dict["avg_rating"] = avg_rating
        restaurant_dict["review_count"] = review_count
        restaurant_dict["stars_percent"] = round((float(avg_rating) / 5.0) * 100) if review_count > 0 else 0
        restaurants.append(restaurant_dict)

    template = jinja_env.get_template("index.html")
    context = {"request": request, "restaurants": restaurants, "url_for": app.url_path_for}
    html_content = template.render(context)
    return HTMLResponse(content=html_content)


@app.get("/create", response_class=HTMLResponse)
async def create_restaurant(request: Request):
    logger.info("Request for add restaurant page received")
    template = jinja_env.get_template("create_restaurant.html")
    context = {"request": request, "url_for": app.url_path_for}
    html_content = template.render(context)
    return HTMLResponse(content=html_content)


@app.post("/add", response_class=RedirectResponse)
async def add_restaurant(
    request: Request,
    restaurant_name: str = Form(...),
    street_address: str = Form(...),
    description: str = Form(...),
    session: Session = Depends(get_db_session),
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

    restaurant_dict = restaurant.model_dump()
    restaurant_dict["avg_rating"] = avg_rating
    restaurant_dict["review_count"] = review_count
    restaurant_dict["stars_percent"] = round((float(avg_rating) / 5.0) * 100) if review_count > 0 else 0

    template = jinja_env.get_template("details.html")
    context = {"request": request, "restaurant": restaurant_dict, "reviews": reviews, "url_for": app.url_path_for}
    html_content = template.render(context)
    return HTMLResponse(content=html_content)


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


@app.post("/delete/{id}", response_class=RedirectResponse)
async def delete_restaurant(
    request: Request,
    id: int,
    session: Session = Depends(get_db_session),
):
    restaurant = session.exec(select(Restaurant).where(Restaurant.id == id)).first()
    if restaurant is None:
        return RedirectResponse(url=app.url_path_for("index"), status_code=status.HTTP_303_SEE_OTHER)

    reviews = session.exec(select(Review).where(Review.restaurant == id)).all()
    for review in reviews:
        session.delete(review)
    session.commit()

    session.delete(restaurant)
    session.commit()

    return RedirectResponse(url=app.url_path_for("index"), status_code=status.HTTP_303_SEE_OTHER)
