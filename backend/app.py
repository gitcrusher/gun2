from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from postgrest import APIError
from supabase import Client, create_client

load_dotenv()

app = Flask(__name__)
# Public API: allow browser requests from any origin. No authentication is used.
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
if not supabase_url or not supabase_key:
    raise RuntimeError("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must be set")

supabase: Client = create_client(supabase_url, supabase_key)


def api_error(message: str, status: int):
    return jsonify({"error": message}), status


def find_one(table: str, row_id: str) -> dict[str, Any] | None:
    response = supabase.table(table).select("*").eq("id", row_id).maybe_single().execute()
    return response.data


@app.get("/")
def index():
    return jsonify({"message": "Events API", "endpoints": [
        "GET /api/events",
        "GET /api/events/<id>",
        "POST /api/registrations",
        "GET /api/me/registrations?user_id=<uuid>",
        "GET /api/events/<id>/registrations?organizer_id=<uuid>",
    ]})


@app.get("/api/events")
def list_events():
    try:
        query = supabase.table("events").select("*")
        status = request.args.get("status")
        if status:
            query = query.eq("status", status)
        response = query.order("title").execute()
        return jsonify(response.data)
    except APIError as error:
        return api_error(error.message, 500)


@app.get("/api/events/<event_id>")
def get_event(event_id: str):
    try:
        event = find_one("events", event_id)
        if event is None:
            return api_error("Event not found", 404)
        return jsonify(event)
    except APIError as error:
        return api_error(error.message, 500)


@app.post("/api/registrations")
def create_registration():
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    event_id = body.get("event_id")
    if not user_id or not event_id:
        return api_error("user_id and event_id are required", 400)

    try:
        event = find_one("events", str(event_id))
        if event is None:
            return api_error("Event not found", 404)
        user = find_one("users", str(user_id))
        if user is None:
            return api_error("User not found", 404)
        if event.get("status", "").lower() not in ("open", "published", "active"):
            return api_error("Event is not accepting registrations", 409)

        existing = (supabase.table("registrations").select("id")
                    .eq("user_id", str(user_id)).eq("event_id", str(event_id))
                    .limit(1).execute())
        if existing.data:
            return jsonify({"error": "Already registered", "registration": existing.data[0]}), 409

        registrations = (supabase.table("registrations").select("id")
                         .eq("event_id", str(event_id)).execute())
        capacity = event.get("capacity")
        if capacity is not None and len(registrations.data) >= capacity:
            return api_error("Event is at capacity", 409)

        response = (supabase.table("registrations")
                    .insert({"user_id": str(user_id), "event_id": str(event_id)})
                    .execute())
        return jsonify(response.data[0]), 201
    except APIError as error:
        # A unique constraint can catch simultaneous duplicate requests.
        if getattr(error, "code", None) == "23505":
            return api_error("Already registered", 409)
        return api_error(error.message, 500)


@app.get("/api/me/registrations")
def my_registrations():
    # With authentication intentionally disabled, the caller supplies the user id.
    user_id = request.args.get("user_id")
    if not user_id:
        return api_error("user_id query parameter is required", 400)
    try:
        response = (supabase.table("registrations").select("*")
                    .eq("user_id", user_id).order("created_at", desc=True).execute())
        return jsonify(response.data)
    except APIError as error:
        return api_error(error.message, 500)


@app.get("/api/events/<event_id>/registrations")
def event_registrations(event_id: str):
    # This id check is not authorization; without auth any caller can supply the organizer id.
    organizer_id = request.args.get("organizer_id")
    if not organizer_id:
        return api_error("organizer_id query parameter is required", 400)
    try:
        event = find_one("events", event_id)
        if event is None:
            return api_error("Event not found", 404)
        if str(event.get("organizer_id")) != organizer_id:
            return api_error("organizer_id does not match this event", 403)
        response = (supabase.table("registrations")
                    .select("id,user_id,event_id,created_at")
                    .eq("event_id", event_id).order("created_at").execute())
        return jsonify(response.data)
    except APIError as error:
        return api_error(error.message, 500)


if __name__ == "__main__":
    app.run(debug=True)
