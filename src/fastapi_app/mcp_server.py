import asyncio
import contextlib
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from sqlalchemy.sql import func
from sqlmodel import Session, select

from .models import Restaurant, Review, engine

# Create a FastMCP server. Use stateless_http=True for simple mounting. Default path is .../mcp
mcp = FastMCP("RestaurantReviewsMCP", stateless_http=True)

# Lifespan context manager to start/stop the MCP session manager with the FastAPI app
@asynccontextmanager
async def mcp_lifespan(app):
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(mcp.session_manager.run())
        yield

# MCP tool: List all restaurants with their average rating and review count
@mcp.tool()
async def list_restaurants_mcp() -> list[dict]:
    """List restaurants with their average rating and review count."""

    def sync():
        with Session(engine) as session:
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
            rows = []
            for restaurant, avg_rating, review_count in results:
                r = restaurant.dict()
                r["avg_rating"] = float(avg_rating) if avg_rating is not None else None
                r["review_count"] = review_count
                r["stars_percent"] = (
                    round((float(avg_rating) / 5.0) * 100) if review_count > 0 and avg_rating is not None else 0
                )
                rows.append(r)
            return rows

    return await asyncio.to_thread(sync)

# MCP tool: Get a restaurant and all its reviews by restaurant_id
@mcp.tool()
async def get_details_mcp(restaurant_id: int) -> dict:
    """Return the restaurant and its related reviews as objects."""

    def sync():
        with Session(engine) as session:
            restaurant = session.exec(select(Restaurant).where(Restaurant.id == restaurant_id)).first()
            if restaurant is None:
                return None
            reviews = session.exec(select(Review).where(Review.restaurant == restaurant_id)).all()
            return {"restaurant": restaurant.dict(), "reviews": [r.dict() for r in reviews]}

    return await asyncio.to_thread(sync)

# MCP tool: Create a new review for a restaurant
@mcp.tool()
async def create_review_mcp(restaurant_id: int, user_name: str, rating: int, review_text: str) -> dict:
    """Create a new review for a restaurant and return the created review dict."""

    def sync():
        with Session(engine) as session:
            review = Review()
            review.restaurant = restaurant_id
            review.review_date = __import__("datetime").datetime.now()
            review.user_name = user_name
            review.rating = int(rating)
            review.review_text = review_text
            session.add(review)
            session.commit()
            session.refresh(review)
            return review.dict()

    return await asyncio.to_thread(sync)

# MCP tool: Create a new restaurant
@mcp.tool()
async def create_restaurant_mcp(restaurant_name: str, street_address: str, description: str) -> dict:
    """Create a new restaurant and return the created restaurant dict."""

    def sync():
        with Session(engine) as session:
            restaurant = Restaurant()
            restaurant.name = restaurant_name
            restaurant.street_address = street_address
            restaurant.description = description
            session.add(restaurant)
            session.commit()
            session.refresh(restaurant)
            return restaurant.dict()

    return await asyncio.to_thread(sync)
