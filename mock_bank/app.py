"""Mock legacy bank back-office portal.

Deliberately hostile to automation the way a real 15-year-old core banking
UI is: server-rendered on every request, table-based layout, no
`data-testid`/`data-cy` attributes anywhere, minimal CSS. The only reliable
way to find a control is the same way a human operator would — its visible
text, its label, or its role in the page (a button, a table cell, a link).

This app is also the error-injection surface: a handful of reserved member
IDs deterministically produce the runtime conditions replay has to detect
(see mock_bank/data.py) instead of relying on flaky real-world timing.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal, InvalidOperation

from flask import Flask, redirect, render_template, request, session, url_for

from mock_bank import data
from mock_bank.data import store

app = Flask(__name__)
app.secret_key = os.environ.get("FLUX_MOCK_BANK_SECRET", "dev-only-not-a-real-secret")

VALID_USERNAME = "operator"
VALID_PASSWORD = "letmein"
MIN_OPENING_DEPOSIT = Decimal("25.00")


def _require_login():
    return session.get("logged_in") is True


@app.route("/")
def index():
    if _require_login():
        return redirect(url_for("search"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    expired = request.args.get("expired") == "1"
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == VALID_USERNAME and password == VALID_PASSWORD:
            session.clear()
            session["logged_in"] = True
            return redirect(url_for("search"))
        error = "Invalid username or password."
    return render_template("login.html", error=error, expired=expired)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/search", methods=["GET", "POST"])
def search():
    if not _require_login():
        return redirect(url_for("login"))
    results = None
    query = ""
    if request.method == "POST":
        query = request.form.get("query", "")
        results = store.search(query)
    return render_template("search.html", results=results, query=query)


@app.route("/member/<member_id>")
def member_detail(member_id: str):
    if not _require_login():
        return redirect(url_for("login"))

    if member_id == data.SESSION_TIMEOUT_ID:
        # Legacy-app-realistic: the session just silently dies server-side and
        # the next screen you see is the login page again, still HTTP 200 -
        # no 401, no redirect chain a naive automation could key off of.
        session.clear()
        return render_template("login.html", error=None, expired=True)

    if member_id == data.SLOW_LOAD_ID:
        time.sleep(data.SLOW_LOAD_SECONDS)

    member = store.get(member_id)
    if member is None:
        return render_template("member_not_found.html", member_id=member_id)

    if member.status == "restricted":
        return render_template("access_denied.html", member_id=member_id)

    return render_template("member_detail.html", member=member)


@app.route("/member/<member_id>/sub-account/new", methods=["GET", "POST"])
def sub_account_new(member_id: str):
    if not _require_login():
        return redirect(url_for("login"))

    member = store.get(member_id)
    if member is None:
        return render_template("member_not_found.html", member_id=member_id)
    if member.status == "restricted":
        return render_template("access_denied.html", member_id=member_id)

    error = None
    account_type = "savings"
    deposit_raw = ""
    if request.method == "POST":
        account_type = request.form.get("account_type", "savings")
        deposit_raw = request.form.get("initial_deposit", "")
        try:
            deposit = Decimal(deposit_raw)
        except InvalidOperation:
            error = "Initial deposit must be a dollar amount, e.g. 100.00."
        else:
            if deposit < MIN_OPENING_DEPOSIT:
                error = f"Minimum opening deposit is ${MIN_OPENING_DEPOSIT:.2f}."
            else:
                session["pending_sub_account"] = {
                    "member_id": member_id,
                    "account_type": account_type,
                    "initial_deposit": str(deposit),
                }
                return redirect(url_for("sub_account_confirm", member_id=member_id))

    return render_template(
        "sub_account_new.html",
        member=member,
        error=error,
        account_type=account_type,
        deposit_raw=deposit_raw,
    )


@app.route("/member/<member_id>/sub-account/confirm", methods=["GET", "POST"])
def sub_account_confirm(member_id: str):
    if not _require_login():
        return redirect(url_for("login"))

    pending = session.get("pending_sub_account")
    if not pending or pending.get("member_id") != member_id:
        return redirect(url_for("sub_account_new", member_id=member_id))

    member = store.get(member_id)
    if member is None:
        return render_template("member_not_found.html", member_id=member_id)

    if request.method == "POST":
        sub = store.open_sub_account(
            member_id, pending["account_type"], Decimal(pending["initial_deposit"])
        )
        session.pop("pending_sub_account", None)
        return render_template("sub_account_success.html", member=member, sub_account=sub)

    return render_template("sub_account_confirm.html", member=member, pending=pending)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, debug=True)
