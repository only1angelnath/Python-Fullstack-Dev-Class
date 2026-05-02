# Building a simple app using fastAPi

from fastapi import FastAPI

# creating an instance of fastapi app
app = FastAPI()

# Windsurf Reflector | Explain | Generate Docstrings
@app.get("/") #Define a route for the root URL
def home():
    return {"message": "Welcome to the FastAPI application!"}

# Check about status
@app.get("/about")
def about_info():
    return {
        "course": "Python Fullstack Development by Analytic Sages",
        "instructor": "Defi__Josh",
        "description": "This course covers how to build a fullstack app using python and FastAPI"
        }

@app.get("/csv")
def csv():
    csv_content