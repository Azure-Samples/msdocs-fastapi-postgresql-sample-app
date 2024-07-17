import os
import pathlib
from datetime import datetime

from sqlmodel import Session, select
from fastapi import FastAPI, Request, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.sql import func

from azure.monitor.opentelemetry import configure_azure_monitor
from .models import Restaurant, Review, engine

if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    configure_azure_monitor()

app = FastAPI()
parent_path = pathlib.Path(__file__).parent.parent
app.mount("/mount", StaticFiles(directory=parent_path / "static"), name="static")
templates = Jinja2Templates(directory=parent_path / "templates")
templates.env.globals["prod"] = os.environ.get("RUNNING_IN_PRODUCTION", False)
# Use relative path for url_for, so that it works behind a proxy like Codespaces
templates.env.globals["url_for"] = app.url_path_for

# Dependency to get the database session
def get_db():
    with Session(engine) as session:
        yield session

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    print("root called")
    statement = (
        select(
            Restaurant,
            func.avg(Review.rating).label("avg_rating"),
            func.count(Review.id).label("review_count")
        )
        .outerjoin(Review, Review.restaurant == Restaurant.id)
        .group_by(Restaurant.id)
    )
    results = db.exec(statement).all()

    restaurants = []
    for restaurant, avg_rating, review_count in results:
        restaurant_dict = restaurant.dict()
        restaurant_dict["avg_rating"] = avg_rating
        restaurant_dict["review_count"] = review_count
        restaurant_dict["stars_percent"] = round((float(avg_rating) / 5.0) * 100) if review_count > 0 else 0
        restaurants.append(restaurant_dict)

    return templates.TemplateResponse("index.html", {"request": request, "restaurants": restaurants})


@app.get('/create', response_class=HTMLResponse)
def create_restaurant(request: Request):
    print('Request for add restaurant page received')
    return templates.TemplateResponse('create_restaurant.html', {"request": request})

@app.post('/add', response_class=RedirectResponse)
async def add_restaurant(request: Request, restaurant_name: str=Form(...), street_address: str=Form(...), description: str=Form(...)):
    print(f"name: {restaurant_name} address: {street_address} description: {description}")
    restaurant = Restaurant()
    restaurant.name = restaurant_name
    restaurant.street_address = street_address
    restaurant.description = description
    with Session(engine) as session:
        session.add(restaurant)
        session.commit()
        session.refresh(restaurant)

    # Dynamically construct the base URL from the request
    scheme = request.url.scheme  # 'http' or 'https'
    host = request.headers.get('x-forwarded-host')  # May need to consider 'x-forwarded-host' if behind a proxy
    base_url = f"{scheme}://{host}"
    redirect_url = f"{base_url}"    

    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@app.get("/details/{id}", response_class=HTMLResponse)
def details(request: Request, id: int):
    with Session(engine) as session:
        restaurant = session.exec(select(Restaurant).where(Restaurant.id == id)).first()
        reviews= session.exec(select(Review).where(Review.restaurant == id)).all()

    review_count = len(reviews)

    avg_rating = 0
    if review_count > 0:
        avg_rating = sum(review.rating for review in reviews if review.rating is not None) / review_count
    
    restaurant_dict = restaurant.dict()
    restaurant_dict["avg_rating"] = avg_rating
    restaurant_dict["review_count"] = review_count
    restaurant_dict["stars_percent"] = round((float(avg_rating) / 5.0) * 100) if review_count > 0 else 0

    return templates.TemplateResponse('details.html', {"request": request, "restaurant":restaurant_dict, "reviews":reviews})

@app.post("/review/{id}", response_class=RedirectResponse)
def add_review(request: Request, id: int, user_name: str=Form(...), rating: str=Form(...), review_text: str=Form(...), db: Session = Depends(get_db) ):
    review = Review()
    review.restaurant = id
    review.review_date = datetime.now()
    review.user_name = user_name
    review.rating = int(rating)
    review.review_text = review_text
    db.add(review)
    db.commit()

    # return RedirectResponse(url=request.url_for('details', id=id), status_code=status.HTTP_303_SEE_OTHER)
    scheme = request.url.scheme  # 'http' or 'https'
    host = request.headers.get('x-forwarded-host')  # May need to consider 'x-forwarded-host' if behind a proxy
    base_url = f"{scheme}://{host}"
    redirect_url = f"{base_url}/details/{id}"  

    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)