# Building a simple app using fastAPi

from fastapi import FastAPI

# creating an instance of fastapi app
app = FastAPI()

Windsurf Reflector | Explain | Generate Docstrings |
@app.get("/") #Define a route for the root URL
def home():
    return {"message": "Welcome to the FastAPI application!"}