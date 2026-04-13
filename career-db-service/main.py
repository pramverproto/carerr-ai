from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_pool, close_pool
from app.routers import health, assessments, candidates, reports, career_plans


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        db=settings.db_name,
        minsize=settings.db_pool_min,
        maxsize=settings.db_pool_max,
    )
    yield
    await close_pool()


app = FastAPI(
    title="Career DB Service",
    description="Read-only database microservice for career-agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(assessments.router)
app.include_router(candidates.router)
app.include_router(reports.router)
app.include_router(career_plans.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )
