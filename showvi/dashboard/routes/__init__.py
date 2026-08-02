from dashboard.routes.core import router as core_router
from dashboard.routes.repository import router as repository_router
from dashboard.routes.creation import router as creation_router
from dashboard.routes.assets import router as assets_router
from dashboard.routes.settings import router as settings_router
from dashboard.routes.video_gen import router as video_gen_router
from dashboard.routes.storyboard import router as storyboard_router
from dashboard.routes.scene import router as scene_router
from dashboard.routes.demo import router as demo_router

all_routers = [
    core_router,
    repository_router,
    creation_router,
    assets_router,
    settings_router,
    video_gen_router,
    storyboard_router,
    scene_router,
    demo_router,
]
