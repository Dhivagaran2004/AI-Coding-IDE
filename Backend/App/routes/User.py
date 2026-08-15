from fastapi import APIRouter, Depends
from App.auth.auth import get_current_user
from App.models.user import User


router = APIRouter(
    prefix="/users",
    tags=["Users"]
)

#get current user
@router.get("/me")
def get_my_profile(
    current_user: User = Depends(get_current_user)
):

    return {
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email
    }
