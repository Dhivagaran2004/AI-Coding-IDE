from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    name: str = Field(..., validation_alias="name")
    email: EmailStr
    password: str

    model_config = {
        "populate_by_name": True,
        "extra": "ignore",
    }

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        if isinstance(obj, dict) and "username" in obj and "name" not in obj:
            obj = {**obj, "name": obj["username"]}
        return super().model_validate(obj, *args, **kwargs)


class UserResponse(BaseModel):
    id: int
    name: str
    email: EmailStr

    class Config:
        from_attributes = True


class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

