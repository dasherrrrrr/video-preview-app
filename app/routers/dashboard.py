from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from ..auth import require_login

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

router = APIRouter()


@router.get("/")
def dashboard(request: Request, user=Depends(require_login)):
    # TODO Teil 2: hier den echten Videokatalog aus der DB laden
    videos = []
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": user, "videos": videos}
    )
