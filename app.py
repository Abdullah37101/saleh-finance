from flask import Flask, flash, redirect, render_template, request, session, send_file, g
from werkzeug.security import check_password_hash, generate_password_hash
from itsdangerous import URLSafeTimedSerializer
import mimetypes
import os
import sqlite3
from datetime import datetime
from email.utils import formataddr
from functools import wraps
from re import match
from zoneinfo import ZoneInfo

import finnhub
import phonenumbers
import requests
from PIL import Image
from pillow_heif import register_heif_opener
import pillow_avif
from dotenv import load_dotenv
from flask import Flask, flash, g, redirect, render_template, request, send_file, session
from flask_mail import Mail, Message
from flask_session import Session
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
load_dotenv()
register_heif_opener()

def market_status(dummy=""):
    try:
        return finnhub_client.market_status(exchange='US')["isOpen"]
    except requests.RequestException as e:
        print(f"Request error: {e}")
    except (KeyError, ValueError) as e:
        print(f"Data parsing error: {e}")

def usd(value):
    return f"${value:,.2f}" if value >= 0 else f"-${-value:,.2f}"

def per(value):
    return f"{abs(value):,.2f}%"

def my_abs(value):
    return f"{abs(value):,.2f}"

app.jinja_env.filters["usd"] = usd
app.jinja_env.filters["my_abs"] = my_abs
app.jinja_env.filters["per"] = per
app.jinja_env.filters["abs"] = abs
app.jinja_env.filters["market"] = market_status

app.config["SESSION_PERMANENT"] = True
app.config["SESSION_TYPE"] = "filesystem"
app.config["UPLOAD_FOLDER"] = "uploads/profile_pictures"
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['MAIL_SERVER'] = "smtp.gmail.com"
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')

Session(app)
mail = Mail(app)
s = URLSafeTimedSerializer(app.config['SECRET_KEY'])

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'jfif', 'pjpeg', 'pjp', 'gif', 'webp', 'avif', 'apng', 'svg', 'bmp', 'ico'}

finnhub_client = finnhub.Client(api_key=f"{os.getenv('API_KEY')}")

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect("finance.db", isolation_level=None)
        g.db.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

    return g.db

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def apology(message, code=400):
    def escape(s):
        for old, new in [
            ("-", "--"),
            (" ", "-"),
            ("_", "__"),
            ("?", "~q"),
            ("%", "~p"),
            ("#", "~h"),
            ("/", "~s"),
            ('"', "''"),
        ]:
            s = s.replace(old, new)
        return s
    return render_template("apology.html", top=code, bottom=escape(message)), code

def login_required(f):
    @wraps(f)

    def decorated_function(*args, **kwargs):
        if session.get("user_id"):
            db = get_db()
            if not db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchall():
                session.clear()
                return redirect("/login")
            session["verified"] = db.execute("SELECT verified FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["verified"]
            if session.get("verified") == 1:
                return f(*args, **kwargs)
        return redirect("/login")
    
    return decorated_function

def login_not_required(f):
    @wraps(f)

    def decorated_function(*args, **kwargs):
        if session.get("user_id"):
            db = get_db()
            if not db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchall():
                session.clear()
                return f(*args, **kwargs)
            session["verified"] = db.execute("SELECT verified FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["verified"]
            if session.get("verified") == 1:
                return redirect("/")
        return f(*args, **kwargs)
    
    return decorated_function

def lookup(symbol):
    try:
        data: dict = finnhub_client.company_profile2(symbol=symbol)
        data.update(finnhub_client.quote(symbol.upper()))

        if data["c"] == 0:
            return None
        
        return {
            "current_price": data["c"],
            "daily_change_percentage": data["dp"],
            "daily_change": data["d"],
            "high": data["h"],
            "low": data["l"],
            "open": data["o"],
            "previous_close": data["pc"],
            "symbol": data["ticker"],
            "name": data["name"],
            "logo": data["logo"],
            "phone": f"+1 { phonenumbers.format_number(phonenumbers.parse(data["phone"], "US"), phonenumbers.PhoneNumberFormat.NATIONAL) }",
            "website_url": data["weburl"],
            "industry": data["finnhubIndustry"],
            "market_capitalization": data["marketCapitalization"] * 1000000,
            "outstanding_shares": f"{int(data["shareOutstanding"] * 1000000):,}"
        }
    except requests.RequestException as e:
        print(f"Request error: {e}")
    except (KeyError, ValueError) as e:
        print(f"Data parsing error: {e}")

def generate_token(email, salt):
    return s.dumps(email, salt=salt)

def confirm_token(token, salt):
    try:
        email = s.loads(token, salt=salt, max_age=3600)
    except:
        return None
    return email

def send_change_email(user_email):
    db = get_db()
    previous_token = generate_token(db.execute("SELECT email FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["email"], "previous-email-change-salt")
    new_token = generate_token(user_email, "new-email-change-salt")
    verification_link = f"https://127.0.0.1:5000/change-email/{previous_token}/{new_token}"

    with open("emails/change_email.html", "r", encoding="utf-8") as f:
        html_content = f.read().replace("{{EMAIL_CONFIRMATION_LINK}}", verification_link).replace("{{NAME}}", db.execute("SELECT name FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["name"])

    msg: Message = Message('$ALEH Finance - Change Your Email', sender=formataddr(("$ALEH Finance", app.config['MAIL_USERNAME'])), recipients=[user_email])
    msg.html = html_content
    mail.send(msg)

def send_confirmation_email(user_email):
    token = generate_token(user_email, "email-verify-salt")
    verification_link = f"https://127.0.0.1:5000/verify-email/{token}"

    with open("emails/verification_email.html", "r", encoding="utf-8") as f:
        db = get_db()
        html_content = f.read().replace("{{VERIFICATION_LINK}}", verification_link).replace("{{NAME}}", db.execute("SELECT name FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["name"])

    msg: Message = Message('$ALEH Finance - Verify Your Email', sender=formataddr(("$ALEH Finance", app.config['MAIL_USERNAME'])), recipients=[user_email])
    msg.html = html_content
    mail.send(msg)

def send_reset_email(user_email):
    token = generate_token(user_email, "email-reset-salt")
    reset_link = f"https://127.0.0.1:5000/reset-password/{token}"

    with open("emails/reset_password_email.html", "r", encoding="utf-8") as f:
        db = get_db()
        html_content = f.read().replace("{{RESET_LINK}}", reset_link).replace("{{NAME}}", db.execute("SELECT name FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["name"])

    msg: Message = Message('$ALEH Finance - Reset Your Password', sender=formataddr(("$ALEH Finance", app.config['MAIL_USERNAME'])), recipients=[user_email])
    msg.html = html_content
    mail.send(msg)

@app.errorhandler(404)
def page_not_found(e):
    return apology("the page you're looking for doesn't exist", 404)

@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()

@app.route("/my-profile")
@login_required
def my_profile():
    db = get_db()
    name = db.execute("SELECT name FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["name"]
    return render_template("my_profile.html", name=name)

@app.route("/profile-picture")
@login_required
def serve_profile_picture():
    db = get_db()
    profile_picture = f"{app.config["UPLOAD_FOLDER"]}/{db.execute("SELECT profile_picture FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["profile_picture"]}"
    mime_type, _ = mimetypes.guess_type(profile_picture)
    try:
        return send_file(profile_picture, mimetype=mime_type or "application/octet-stream")
    except:
        db.execute("UPDATE users SET profile_picture = ? WHERE id = ?", ("default.jpg", session["user_id"]))
        return serve_profile_picture()

@app.route("/change-picture", methods=["POST"])
@login_required
def change_picture():
    if "profile_picture" not in request.files:
        return apology("no picture provided")
    
    file = request.files["profile_picture"]
    if file.filename == "":
        return apology("no picture provided")
    
    try:
        if not file:
            raise Exception
        try:
            if file.filename.rsplit('.', 1)[1].lower() == "webp":
                raise Exception
            
            filename = f"{session["user_id"]}.webp"
            Image.open(file.stream).save(f"{app.config["UPLOAD_FOLDER"]}/{filename}", format="webp", save_all=True)
        except:
            if not allowed_file(file.filename):
                raise Exception
            file.stream.seek(0)
            filename = f"{session["user_id"]}.{file.filename.rsplit('.', 1)[1].lower()}"
            file.save(f"{app.config["UPLOAD_FOLDER"]}/{filename}")

        db = get_db()
        previous = db.execute("SELECT profile_picture FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["profile_picture"]
        if previous != "default.jpg" and previous != filename:
            os.remove(f"{app.config["UPLOAD_FOLDER"]}/{previous}")

        db.execute("UPDATE users SET profile_picture = ? WHERE id = ?", (filename, session["user_id"]))
        return redirect("/my-profile")
    except:
        return apology("unsupported image format", 400)

@app.route("/")
@login_required
def index():
    db = get_db()
    stocks = db.execute("SELECT symbol, SUM(shares) AS total_shares FROM transactions WHERE user_id = ? GROUP BY symbol", (session["user_id"],)).fetchall()
    cash = db.execute("SELECT cash FROM users WHERE id = ?",(session["user_id"],)).fetchall()[0]["cash"]
    total_assets = cash
    securities = 0

    for stock in stocks:
        if stock["total_shares"] <= 0:
            stocks.remove(stock)
            continue

        buys = db.execute("SELECT shares, price FROM transactions WHERE symbol = ? AND user_id = ? AND shares > 0 ORDER BY date", (stock["symbol"], session["user_id"])).fetchall()
        stock["total_cost"] = 0
        for buy in buys:
            stock["total_cost"] += buy["shares"] * buy["price"]

        shares_to_sell = -db.execute("SELECT SUM(shares) AS shares_to_sell FROM transactions WHERE symbol = ? AND user_id = ? AND shares < 0 GROUP BY symbol", (stock["symbol"], session["user_id"])).fetchall()[0]["shares_to_sell"] if db.execute("SELECT * FROM transactions WHERE symbol = ? AND user_id = ? AND shares < 0", (stock["symbol"], session["user_id"])).fetchall() else 0
        while True:
            if buys[0]["shares"] <= shares_to_sell:
                shares_to_sell -= buys[0]["shares"]
                stock["total_cost"] -= buys[0]["shares"] * buys[0]["price"]
                buys.pop(0)
            else:
                buys[0]["shares"] -= shares_to_sell
                stock["total_cost"] -= shares_to_sell * buys[0]["price"]
                break

        stock.update(lookup(stock["symbol"]))
        stock["cost_per_unit"] = stock["total_cost"] / stock["total_shares"]
        stock["market_value"] = stock["current_price"] * stock["total_shares"]
        stock["unrealized_return"] = stock["market_value"] - stock["total_cost"]

        securities += stock["market_value"]
    total_assets += securities

    return render_template("index.html", stocks=stocks, cash=cash, total_assets=total_assets, securities=securities)

@app.route("/buy", methods=["GET", "POST"])
@login_required
def buy():
    if not market_status():
        return apology("market closed", 400)
    

    if request.method == "GET":
        return render_template("buy.html")
    

    db = get_db()
    symbol = request.form.get("symbol").upper()
    if not symbol:
        return apology("missing symbol", 400)
    
    if lookup(symbol) is None:
        return apology("invalid symbol", 400)
    
    shares = request.form.get("shares")
    if not shares:
        return apology("missing shares", 400)
    
    if not shares.isdigit() or int(shares) < 1:
        return apology("invalid shares", 400)
    
    shares = int(shares)
    
    current_price = lookup(symbol)["current_price"]
    cash = db.execute("SELECT cash FROM users WHERE id = ?",(session["user_id"],)).fetchall()[0]["cash"] - shares * current_price
    if cash < 0:
        return apology("can't afford", 400)
    
    db.execute("UPDATE users SET cash = ? WHERE id = ?", (cash, session["user_id"]))
    db.execute("INSERT INTO transactions (user_id, symbol, shares, price, date) VALUES (?, ?, ?, ?, ?)",(session["user_id"], symbol, shares, current_price, datetime.now(ZoneInfo("America/New_York"))))
    flash("Bought!")
    return redirect("/")


@app.route("/history")
@login_required
def history():
    db = get_db()
    transactions = db.execute("SELECT symbol, shares, price, date FROM transactions WHERE user_id = ? ORDER BY date DESC", (session["user_id"],)).fetchall()
    for transaction in transactions:
        transaction["logo"] = lookup(transaction["symbol"])["logo"]
        transaction["date"] = datetime.fromisoformat(transaction["date"]).strftime("%m/%d/%Y %I:%M %p")

    return render_template("history.html", transactions=transactions)


@app.route("/login", methods=["GET", "POST"])
@login_not_required
def login():
    if request.method == "GET":
        return render_template("login.html")
    
    if not request.form.get("email"):
        return apology("missing email", 400)
    
    if not request.form.get("password"):
        return apology("missing password", 400)
    
    db = get_db()
    user = db.execute("SELECT id, hash FROM users WHERE email = ? AND verified = 1", (request.form.get("email"),)).fetchall()
    if not user:
        return apology("invalid user", 400)
    
    if not check_password_hash(user[0]["hash"], request.form.get("password")):
        return apology("incorrect password", 403)
    
    session["user_id"] = user[0]["id"]
    session["verified"] = 1
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/delete-account", methods=["GET", "POST"])
@login_required
def delete_account():
    if request.method == "GET":
        return render_template("delete_account.html")
    
    if not request.form.get("password"):
        return apology("missing password", 403)
    
    db = get_db()
    if not check_password_hash(db.execute("SELECT hash FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["hash"], request.form.get("password")):
        return apology("incorrect password", 403)
    
    profile_picture = db.execute("SELECT profile_picture FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["profile_picture"]
    if profile_picture != "default.jpg":
        os.remove(f"{app.config["UPLOAD_FOLDER"]}/{profile_picture}")
    
    db.execute("DELETE FROM users WHERE id = ?", (session["user_id"],))
    db.execute("DELETE FROM transactions WHERE user_id = ?", (session["user_id"],))
    session.clear()
    flash("Account Deleted!")
    return redirect("/login")

@app.route("/change-name", methods=["GET", "POST"])
@login_required
def change_name():
    if request.method == "GET":
        return render_template("change_name.html")
    
    if not request.form.get("name"):
        return apology("missing name", 400)
    
    db = get_db()
    db.execute("UPDATE users SET name = ? WHERE id = ?", (request.form.get("name"), session["user_id"]))
    return redirect("/my-profile")

@app.route("/change-email", methods=["GET", "POST"])
@login_required
def change_email():
    if request.method == "GET":
        return render_template("change_email.html")
    

    email = request.form.get("email")
    if not email:
        return apology("missing new email", 400)
    
    if not match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return apology("invalid email", 400)
    
    db = get_db()
    if db.execute("SELECT email FROM users WHERE email = ?", (email,)).fetchall() and db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchall()[0]["id"] == session["user_id"]:
        return apology("email can't be the existing one", 400)
    
    if db.execute("SELECT email FROM users WHERE email = ?", (email,)).fetchall():
        return apology("email taken", 400)

    if not request.form.get("password"):
        return apology("missing password", 400)
    
    if not check_password_hash(db.execute("SELECT hash FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["hash"], request.form.get("password")):
        return apology("incorrect password", 403)
    
    send_change_email(email)
    flash("A verification link has been sent to your new email!")
    return redirect("/")

@app.route("/change-email/<previous_token>/<new_token>")
def change_email_token(previous_token, new_token):
    previous_email = confirm_token(previous_token, "previous-email-change-salt")
    new_email = confirm_token(new_token, "new-email-change-salt")

    db = get_db()
    if not previous_email or not new_email or not db.execute("SELECT email FROM users WHERE email = ?", (previous_email,)).fetchall() or db.execute("SELECT email FROM users WHERE email = ?", (new_email,)).fetchall():
        return apology("the verification link is invalid or has expired", 400)

    db.execute("UPDATE users SET email = ? WHERE email = ?", (new_email, previous_email))
    flash("Email Changed!")
    return redirect("/")

@app.route("/quote")
@login_required
def quote():
    return render_template("quote.html")

@app.route("/quote/<symbol>")
@login_required
def quote_symbol(symbol):
    if lookup(symbol) is None:
        return apology("invalid symbol", 400)
    
    return render_template("quoted.html", stock=lookup(symbol))


@app.route("/register", methods=["GET", "POST"])
@login_not_required
def register():
    if request.method == "GET":
        return render_template("register.html")
    

    name = request.form.get("name")
    if not name:
        return apology("missing name", 400)
    
    email = request.form.get("email")
    if not email:
        return apology("missing email", 400)
    
    if not match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return apology("invalid email", 400)
    
    password = request.form.get("password")
    if not password:
        return apology("missing password", 400)
    
    if not password == request.form.get("confirmation"):
        return apology("passwords don't match", 400)
    
    if len(password) < 8:
        return apology("short password", 400)
    
    db = get_db()
    if db.execute("SELECT * FROM users WHERE email = ? AND verified = 1", (email,)).fetchall():
        return apology("email taken", 400)
    
    if not db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchall():
        db.execute("INSERT INTO users (email, name, hash) VALUES (?, ?, ?)", (email, name, generate_password_hash(password)))

    session["user_id"] = db.execute("SELECT id, verified FROM users WHERE email = ?", (request.form.get("email"),)).fetchall()[0]["id"]
    session["verified"] = 0
    send_confirmation_email(email)
    flash("A verification link has been sent to your email!")
    return redirect("/")

@app.route("/sell", methods=["GET", "POST"])
@login_required
def sell():
    if not market_status():
        return apology("market closed", 400)
    

    db = get_db()

    if request.method == "GET":
        stocks = db.execute("SELECT symbol, SUM(shares) AS total_shares FROM transactions WHERE user_id = ? GROUP BY symbol", (session["user_id"],)).fetchall()
        for stock in stocks:
            if stock["total_shares"] <= 0:
                stocks.remove(stock)

        return render_template("sell.html", stocks=stocks)
    

    symbol = request.form.get("symbol").upper()
    if not symbol:
        return apology("missing symbol", 400)
    
    if lookup(symbol) is None:
        return apology("invalid symbol", 400)
    
    shares = request.form.get("shares")
    if not shares:
        return apology("missing shares", 400)
    
    if not shares.isdigit() or int(shares) < 1:
        return apology("invalid shares", 400)
    
    shares = int(shares)
    
    if not db.execute("SELECT symbol FROM transactions WHERE user_id = ?", (session["user_id"],)).fetchall():
        return apology("you don't own this symbol", 400)
    
    if db.execute("SELECT SUM(shares) AS total_shares FROM transactions WHERE user_id = ? AND symbol = ? GROUP BY symbol", (session["user_id"], symbol,)).fetchall()[0]["total_shares"] < shares:
        return apology("too many shares", 400)
    
    current_price = lookup(symbol)["current_price"]
    cash = db.execute("SELECT cash FROM users WHERE id = ?",(session["user_id"],)).fetchall()[0]["cash"] + shares * current_price
    db.execute("UPDATE users SET cash = ? WHERE id = ?", (cash, session["user_id"]))
    db.execute("INSERT INTO transactions (user_id, symbol, shares, price, date) VALUES (?, ?, ?, ?, ?)",(session["user_id"], symbol, -shares, current_price, datetime.now(ZoneInfo("America/New_York"))))
    
    flash("Sold!")
    return redirect("/")


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "GET":
        return render_template("change_password.html")
    

    old_password = request.form.get("old_password")
    if not old_password:
        return apology("missing old password", 400)
    
    new_password = request.form.get("new_password")
    if not new_password:
        return apology("missing new password", 400)
    
    if not new_password == request.form.get("confirmation"):
        return apology("passwords don't match", 400)
    
    if len(new_password) < 8:
        return apology("short password", 400)
    
    db = get_db()
    correct_old_password = db.execute("SELECT hash FROM users WHERE id = ?", (session["user_id"],)).fetchall()[0]["hash"]
    if not check_password_hash(correct_old_password, old_password):
        return apology("incorrect password", 403)
    
    if check_password_hash(correct_old_password, new_password):
        return apology("new password can't be the existing one", 400)
    
    new_password = generate_password_hash(new_password)
    db.execute("UPDATE users SET hash = ? WHERE id = ?", (new_password, session["user_id"],))
    flash("Password Changed!")
    return redirect("/")

@app.route("/verify-email/<token>")
def confirm_email(token):
    email = confirm_token(token, "email-verify-salt")
    db = get_db()

    if not email or not db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchall():
        return apology("the verification link is invalid or has expired", 400)
    
    if not session.get("verified") or session.get("verified") == 0:
        session["user_id"] = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchall()[0]["id"]
        flash("Registered!")
    else:
        flash(f"{db.execute("SELECT name FROM users WHERE email = ?", (email,)).fetchall()[0]["name"]} Verified!")

    db.execute("UPDATE users SET verified = 1 WHERE email = ?", (email,))
    return redirect("/")
    
@app.route("/forgot-password", methods=["GET", "POST"])
@login_not_required
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")
    

    email = request.form.get("email")

    if not email:
        return apology("missing email", 400)
    
    if not match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return apology("invalid email", 400)
    
    db = get_db()
    if not db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchall():
        return apology("invalid user", 400)
    
    send_reset_email(email)
    flash("A reset link has been sent to your email!")
    return redirect("/")

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_link(token):
    db = get_db()


    if request.method == "GET":
        email = confirm_token(token, "email-reset-salt")

        if not email or not db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchall():
            session.pop("user_to_reset", default=None)
            return apology("the reset link is invalid or has expired", 400)
            
        session["user_to_reset"] = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchall()[0]["id"]
        return render_template("reset_password.html", token=token)
    
    if not db.execute("SELECT id FROM users WHERE id = ?", (session["user_to_reset"])).fetchall():
        session.pop("user_to_reset", default=None)
        return apology("the reset link is invalid or has expired", 400)
    
    new_password = request.form.get("new_password")
    if not new_password:
        return apology("missing new password", 400)
    
    if not new_password == request.form.get("confirmation"):
        return apology("passwords don't match", 400)
    
    if len(new_password) < 8:
        return apology("short password", 400)
    
    old_password = db.execute("SELECT hash FROM users WHERE id = ?", (session["user_to_reset"],)).fetchall()[0]["hash"]
    if check_password_hash(old_password, new_password):
        return apology("password can't be the existing one", 400)
    
    new_password = generate_password_hash(new_password)
    db.execute("UPDATE users SET hash = ? WHERE id = ?", (new_password, session["user_to_reset"]))
    session.pop("user_to_reset", default=None)
    flash("Password Reset!")
    return redirect("/")


if __name__ == "__main__":
    app.run(host=("0.0.0.0"))