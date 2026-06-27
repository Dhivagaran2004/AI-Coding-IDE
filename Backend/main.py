from fastapi import FastAPI

app = FastAPI(
    title="Coding IDE",
    version="1.0.0")

@app.get("/")
def home():
    print("hello")
    return {"Message":"Coding IDE"}


