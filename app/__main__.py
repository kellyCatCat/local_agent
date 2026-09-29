"""启动：python -m app"""
import uvicorn

from .config import settings

if __name__ == "__main__":
    print(f"Skill 维护 Agent: http://{settings.host}:{settings.port}")
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)
