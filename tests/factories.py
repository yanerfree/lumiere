"""
Test data factories — faker-based, with overrides support.
"""
from uuid import uuid4
from faker import Faker

fake = Faker("zh_CN")


def make_user(**overrides) -> dict:
    """Generate a user dict for API requests."""
    data = {
        "username": f"test_{fake.user_name()}_{uuid4().hex[:6]}",
        "password": "Test@123456",
        "role": "user",
    }
    data.update(overrides)
    return data


def make_project(**overrides) -> dict:
    """Generate a project dict for API requests."""
    data = {
        "name": f"proj-{fake.word()}-{uuid4().hex[:6]}",
        "description": fake.sentence(),
        "gitUrl": f"git@code.example.com:team/{fake.word()}.git",
        "scriptPath": f"/workspace/repos/{fake.word()}",
    }
    data.update(overrides)
    return data


def make_member(**overrides) -> dict:
    """Generate a project member dict."""
    data = {
        "role": "member",
    }
    data.update(overrides)
    return data
