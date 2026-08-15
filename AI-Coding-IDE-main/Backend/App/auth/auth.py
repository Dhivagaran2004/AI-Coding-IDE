from  datetime import datetime,timedelta
from jose import JWTError,jwt
from passlib.context import CryptContext
from App.config import(SECRET_KEY,ALGORITHM,ACCESS_TOKEN_EXPIRE_MINUTES)
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from App.database.database import get_db
from App.models.user import User




# --------------------------------------------------
# Password Hashing
# --------------------------------------------------

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


# --------------------------------------------------
# Bearer Token
# --------------------------------------------------

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# --------------------------------------------------
# Hash Password
# --------------------------------------------------

def hash_password(password: str):
    return pwd_context.hash(password)


# --------------------------------------------------
# Verify Password
# --------------------------------------------------

def verify_password(
    plain_password: str,
    hashed_password: str
):
    return pwd_context.verify(
        plain_password,
        hashed_password
    )


# --------------------------------------------------
# Create JWT Token
# --------------------------------------------------

def create_access_token(data: dict):

    to_encode = data.copy()

    expire = datetime.utcnow() + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    to_encode.update({
        "exp": expire
    })

    encoded_jwt = jwt.encode(
        to_encode,
        SECRET_KEY,
        algorithm=ALGORITHM
    )

    return encoded_jwt


# --------------------------------------------------
# Get Current User
# --------------------------------------------------

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={
            "WWW-Authenticate": "Bearer"
        }
    )

    try:

        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM]
        )

        email = payload.get("sub")

        if email is None:
            raise credentials_exception

    except JWTError:

        raise credentials_exception

    user = (
        db.query(User)
        .filter(User.email == email)
        .first()
    )

    if user is None:
        raise credentials_exception

    return user