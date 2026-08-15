from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from App.database.database import get_db
from App.models.user import User
from App.schema.User_Schema import UserCreate, UserResponse, UserLogin, Token
from App.auth.auth import hash_password , verify_password , create_access_token , get_current_user


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)


def _login_with_credentials(
    email: str,
    password: str,
    db: Session
) -> dict:
    db_user = (
        db.query(User)
        .filter(User.email == email)
        .first()
    )

    if not db_user:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )

    if not verify_password(password, db_user.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password"
        )

    access_token = create_access_token({"sub": db_user.email})

    return {
        "access_token": access_token,
        "token_type": "bearer"
    }

#register user
@router.post(
    "/register",
    response_model=UserResponse
)
def register_user(
    user: UserCreate,
    db: Session = Depends(get_db)
):
    existing_user = (
        db.query(User)
        .filter(User.email == user.email)
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )

    new_user = User(
        name=user.name,
        email=user.email,
        password_hash=hash_password(user.password)
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return new_user


#login user (OAuth2 form - used by Swagger Authorize)
@router.post(
    "/login",
    response_model=Token
)
def login_user(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    return _login_with_credentials(
        email=form_data.username,
        password=form_data.password,
        db=db
    )


#login user (JSON)
@router.post(
    "/login/json",
    response_model=Token
)
def login_user_json(
    user: UserLogin,
    db: Session = Depends(get_db)
):
    return _login_with_credentials(
        email=user.email,
        password=user.password,
        db=db
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



    