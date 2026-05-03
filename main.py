# Building a simple app using fastAPi

from fastapi import FastAPI

# creating an instance of fastapi app
app = FastAPI()

# Windsurf Reflector | Explain | Generate Docstrings
@app.get("/") #Define a route for the root URL
def home():
    return {"message": "Welcome to the FastAPI application!",
            "endpoints": ["/about", "/csv"]}

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
    csv_content = "name, age, city\n Alice, 30, New York\n Bob, 25, Los Angeles\n Charlie,35,Chicago"
    return {"csv_data": csv_content}

