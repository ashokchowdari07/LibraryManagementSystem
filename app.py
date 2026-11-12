from flask import Flask, render_template, redirect, url_for, request, session, flash
import sqlite3
import os
from datetime import date, timedelta
from functools import wraps
from werkzeug.security import check_password_hash

import tempfile

# ─── App Setup ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static")
)
app.secret_key = "library_secret_key_2024"   # Used to keep login sessions secure

# On Vercel / Serverless environments, the filesystem is strictly read-only except tempdir (/tmp on Linux)
if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    DATABASE = os.path.join(tempfile.gettempdir(), "database.db")
else:
    DATABASE = os.path.join(BASE_DIR, "database.db")   # Local SQLite database path

# ─── Database Helper ─────────────────────────────────────────────────────────
def get_db():
    """Open a connection to the SQLite database with row factory enabled."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row   # Lets us access columns by name (e.g. row["title"])
    return conn

def init_db():
    """Create tables if they don't exist yet and ensure schema integrity."""
    conn = get_db()
    cursor = conn.cursor()

    # Admin users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT    NOT NULL UNIQUE,
            password TEXT    NOT NULL
        )
    """)

    # Books table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            title     TEXT    NOT NULL,
            author    TEXT    NOT NULL,
            category  TEXT,
            isbn      TEXT,
            quantity  INTEGER DEFAULT 1,
            available INTEGER DEFAULT 1
        )
    """)

    # Add category column if upgrading from an older database
    try:
        cursor.execute("ALTER TABLE books ADD COLUMN category TEXT")
        conn.commit()
    except Exception:
        pass   # Column already exists

    # Library members table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            student_id TEXT,
            email      TEXT NOT NULL UNIQUE,
            phone      TEXT
        )
    """)

    # Add student_id column if upgrading from an older database
    try:
        cursor.execute("ALTER TABLE members ADD COLUMN student_id TEXT")
        conn.commit()
    except Exception:
        pass   # Column already exists

    # Issued books (loan) table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS issued_books (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id     INTEGER NOT NULL REFERENCES books(id),
            member_id   INTEGER NOT NULL REFERENCES members(id),
            issue_date  TEXT    NOT NULL,
            due_date    TEXT    NOT NULL,
            return_date TEXT,
            fine        REAL    DEFAULT 0,
            status      TEXT    NOT NULL DEFAULT 'issued'
        )
    """)

    # Seed a default admin account (username: admin / password: admin123)
    cursor.execute("""
        INSERT OR IGNORE INTO users (username, password) VALUES ('admin', 'admin123')
    """)

    # Pre-seed starter catalog if empty (ensures clean demo state on fresh deployments)
    book_count = cursor.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    if book_count == 0:
        cursor.execute("""
            INSERT INTO books (title, author, category, isbn, quantity, available) VALUES
            ('The Alchemist', 'Paulo Coelho', 'Fiction', '978-0062315007', 5, 5),
            ('Clean Code', 'Robert C. Martin', 'Technology', '978-0132350884', 3, 3),
            ('To Kill a Mockingbird', 'Harper Lee', 'Classic', '978-0446310789', 4, 4)
        """)

    member_count = cursor.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    if member_count == 0:
        cursor.execute("""
            INSERT INTO members (name, student_id, email, phone) VALUES
            ('Rahul Sharma', 'STU2024001', 'rahul.test@example.com', '9876543210')
        """)

    conn.commit()
    conn.close()

# Auto-initialize database safely on startup
try:
    init_db()
except Exception as e:
    print(f"Initial database setup notice: {e}")

# Ensure database tables exist before handling any request (vital for serverless cold starts)
@app.before_request
def ensure_db():
    if not os.path.exists(DATABASE):
        try:
            init_db()
        except Exception as e:
            print(f"ensure_db notice: {e}")

# ─── Auth Decorator ──────────────────────────────────────────────────────────
def login_required(f):
    """Decorator to require login for protected routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in session:
            flash("Please sign in to access this page.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Redirect home page to dashboard if logged in, otherwise to login."""
    if "user" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Show the login form and handle login submissions."""
    if "user" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Please enter both username and password.", "error")
            return render_template("login.html")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        conn.close()

        # Check credentials: supports plaintext (default) and hashed passwords
        is_valid = False
        if user:
            if user["password"] == password:
                is_valid = True
            else:
                try:
                    is_valid = check_password_hash(user["password"], password)
                except Exception:
                    is_valid = False

        if is_valid:
            session["user"] = username
            flash(f"Welcome back, {username}!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Clear the session and redirect back to login."""
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    """Show the main dashboard with summary counts."""
    today = date.today().isoformat()
    conn = get_db()
    total_books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    total_members = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    total_issued = conn.execute(
        "SELECT COUNT(*) FROM issued_books WHERE status = 'issued'"
    ).fetchone()[0]
    total_overdue = conn.execute(
        "SELECT COUNT(*) FROM issued_books WHERE status = 'issued' AND due_date < ?",
        (today,)
    ).fetchone()[0]
    conn.close()

    return render_template(
        "dashboard.html",
        total_books=total_books,
        total_members=total_members,
        total_issued=total_issued,
        total_overdue=total_overdue
    )


@app.route("/books")
@login_required
def books():
    """Show the full list of books."""
    conn = get_db()
    all_books = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("books.html", books=all_books)


@app.route("/books/add", methods=["POST"])
@login_required
def add_book():
    """Add a new book to the database."""
    title    = request.form.get("title", "").strip()
    author   = request.form.get("author", "").strip()
    category = request.form.get("category", "").strip()
    isbn     = request.form.get("isbn", "").strip()
    quantity = request.form.get("quantity", "1").strip()

    # ── Validation ────────────────────────────────────────────
    if not title or not author:
        flash("Title and Author are required.", "error")
        return redirect(url_for("books"))

    if not quantity.isdigit() or int(quantity) < 0:
        flash("Quantity must be a non-negative whole number.", "error")
        return redirect(url_for("books"))

    qty = int(quantity)

    conn = get_db()
    conn.execute(
        "INSERT INTO books (title, author, category, isbn, quantity, available) VALUES (?, ?, ?, ?, ?, ?)",
        (title, author, category or None, isbn or None, qty, qty)
    )
    conn.commit()
    conn.close()

    flash(f'Book "{title}" added successfully!', "success")
    return redirect(url_for("books"))


@app.route("/books/edit/<int:book_id>", methods=["POST"])
@login_required
def edit_book(book_id):
    """Update an existing book's details while preserving active loan tracking."""
    title    = request.form.get("title", "").strip()
    author   = request.form.get("author", "").strip()
    category = request.form.get("category", "").strip()
    isbn     = request.form.get("isbn", "").strip()
    quantity = request.form.get("quantity", "1").strip()

    # ── Validation ────────────────────────────────────────────
    if not title or not author:
        flash("Title and Author are required.", "error")
        return redirect(url_for("books"))

    if not quantity.isdigit() or int(quantity) < 0:
        flash("Quantity must be a non-negative whole number.", "error")
        return redirect(url_for("books"))

    qty = int(quantity)

    conn = get_db()
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if not book:
        conn.close()
        flash("Book not found.", "error")
        return redirect(url_for("books"))

    # Count active issued loans for this book
    active_loans = conn.execute(
        "SELECT COUNT(*) FROM issued_books WHERE book_id = ? AND status = 'issued'",
        (book_id,)
    ).fetchone()[0]

    if qty < active_loans:
        conn.close()
        flash(f'Cannot set quantity to {qty}: {active_loans} copy/copies are currently issued out.', "error")
        return redirect(url_for("books"))

    # Calculate new available count based on active loans
    new_available = qty - active_loans

    conn.execute(
        "UPDATE books SET title=?, author=?, category=?, isbn=?, quantity=?, available=? WHERE id=?",
        (title, author, category or None, isbn or None, qty, new_available, book_id)
    )
    conn.commit()
    conn.close()

    flash(f'Book "{title}" updated successfully!', "success")
    return redirect(url_for("books"))


@app.route("/books/delete/<int:book_id>", methods=["POST"])
@login_required
def delete_book(book_id):
    """Delete a book from the database if no copies are currently loaned out."""
    conn = get_db()
    book = conn.execute("SELECT title FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        conn.close()
        flash("Book not found.", "error")
        return redirect(url_for("books"))

    # Check for active loans
    active_loans = conn.execute(
        "SELECT COUNT(*) FROM issued_books WHERE book_id = ? AND status = 'issued'",
        (book_id,)
    ).fetchone()[0]

    if active_loans > 0:
        conn.close()
        flash(f'Cannot delete "{book["title"]}" because {active_loans} copy/copies are currently issued to members. Please have them returned first.', "error")
        return redirect(url_for("books"))

    conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    conn.commit()
    conn.close()

    flash(f'Book "{book["title"]}" deleted.', "info")
    return redirect(url_for("books"))


@app.route("/members")
@login_required
def members():
    """Show the full list of library members."""
    conn = get_db()
    all_members = conn.execute("SELECT * FROM members ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("members.html", members=all_members)


@app.route("/members/add", methods=["POST"])
@login_required
def add_member():
    """Register a new library member."""
    name       = request.form.get("name",       "").strip()
    student_id = request.form.get("student_id", "").strip()
    email      = request.form.get("email",      "").strip()
    phone      = request.form.get("phone",      "").strip()

    # ── Validation ───────────────────────────────────────────
    if not name or not email:
        flash("Full Name and Email are required.", "error")
        return redirect(url_for("members"))

    if "@" not in email or "." not in email:
        flash("Please provide a valid email address.", "error")
        return redirect(url_for("members"))

    conn = get_db()

    # Check for duplicate Student ID (case-insensitive, if provided)
    if student_id:
        existing = conn.execute(
            "SELECT id FROM members WHERE LOWER(student_id) = LOWER(?)", (student_id,)
        ).fetchone()
        if existing:
            conn.close()
            flash(f'Student ID "{student_id}" is already registered.', "error")
            return redirect(url_for("members"))

    # Check for duplicate Email (case-insensitive)
    existing_email = conn.execute(
        "SELECT id FROM members WHERE LOWER(email) = LOWER(?)", (email,)
    ).fetchone()
    if existing_email:
        conn.close()
        flash(f'Email "{email}" is already registered.', "error")
        return redirect(url_for("members"))

    conn.execute(
        "INSERT INTO members (name, student_id, email, phone) VALUES (?, ?, ?, ?)",
        (name, student_id or None, email, phone or None)
    )
    conn.commit()
    conn.close()

    flash(f'Member "{name}" registered successfully!', "success")
    return redirect(url_for("members"))


@app.route("/members/edit/<int:member_id>", methods=["POST"])
@login_required
def edit_member(member_id):
    """Update an existing member's details."""
    name       = request.form.get("name",       "").strip()
    student_id = request.form.get("student_id", "").strip()
    email      = request.form.get("email",      "").strip()
    phone      = request.form.get("phone",      "").strip()

    # ── Validation ───────────────────────────────────────────
    if not name or not email:
        flash("Full Name and Email are required.", "error")
        return redirect(url_for("members"))

    if "@" not in email or "." not in email:
        flash("Please provide a valid email address.", "error")
        return redirect(url_for("members"))

    conn = get_db()
    member = conn.execute("SELECT id FROM members WHERE id = ?", (member_id,)).fetchone()
    if not member:
        conn.close()
        flash("Member not found.", "error")
        return redirect(url_for("members"))

    # Duplicate Student ID check — exclude the member being edited
    if student_id:
        existing = conn.execute(
            "SELECT id FROM members WHERE LOWER(student_id) = LOWER(?) AND id != ?",
            (student_id, member_id)
        ).fetchone()
        if existing:
            conn.close()
            flash(f'Student ID "{student_id}" is already used by another member.', "error")
            return redirect(url_for("members"))

    # Duplicate Email check — exclude the member being edited
    existing_email = conn.execute(
        "SELECT id FROM members WHERE LOWER(email) = LOWER(?) AND id != ?",
        (email, member_id)
    ).fetchone()
    if existing_email:
        conn.close()
        flash(f'Email "{email}" is already used by another member.', "error")
        return redirect(url_for("members"))

    conn.execute(
        "UPDATE members SET name=?, student_id=?, email=?, phone=? WHERE id=?",
        (name, student_id or None, email, phone or None, member_id)
    )
    conn.commit()
    conn.close()

    flash(f'Member "{name}" updated successfully!', "success")
    return redirect(url_for("members"))


@app.route("/members/delete/<int:member_id>", methods=["POST"])
@login_required
def delete_member(member_id):
    """Delete a member from the database if they have no active loans."""
    conn = get_db()
    member = conn.execute("SELECT name FROM members WHERE id=?", (member_id,)).fetchone()
    if not member:
        conn.close()
        flash("Member not found.", "error")
        return redirect(url_for("members"))

    # Check for active loans
    active_loans = conn.execute(
        "SELECT COUNT(*) FROM issued_books WHERE member_id = ? AND status = 'issued'",
        (member_id,)
    ).fetchone()[0]

    if active_loans > 0:
        conn.close()
        flash(f'Cannot remove "{member["name"]}" because they currently have {active_loans} book(s) issued. Please return them first.', "error")
        return redirect(url_for("members"))

    conn.execute("DELETE FROM members WHERE id=?", (member_id,))
    conn.commit()
    conn.close()

    flash(f'Member "{member["name"]}" removed.', "info")
    return redirect(url_for("members"))


@app.route("/issued")
@login_required
def issued():
    """Show all issued / borrowed books."""
    today = date.today().isoformat()
    conn  = get_db()

    # LEFT JOIN preserves history even if a book/member with returned loans was removed
    rows = conn.execute("""
        SELECT
            ib.id          AS issue_id,
            COALESCE(b.title, '[Deleted Book]') AS book_title,
            COALESCE(m.name, '[Deleted Member]') AS member_name,
            COALESCE(m.student_id, '')   AS student_id,
            ib.issue_date,
            ib.due_date,
            ib.return_date,
            ib.fine,
            ib.status
        FROM issued_books ib
        LEFT JOIN books   b ON b.id = ib.book_id
        LEFT JOIN members m ON m.id = ib.member_id
        ORDER BY ib.id DESC
    """).fetchall()

    # Drop-down data for the Issue form: only books with available stock
    available_books = conn.execute(
        "SELECT id, title, author, available FROM books WHERE available > 0 ORDER BY title"
    ).fetchall()
    all_members = conn.execute(
        "SELECT id, name, student_id FROM members ORDER BY name"
    ).fetchall()
    conn.close()

    return render_template(
        "issued.html",
        issued_list=rows,
        available_books=available_books,
        all_members=all_members,
        today=today
    )


@app.route("/issued/borrow", methods=["POST"])
@login_required
def borrow_book():
    """Issue a book to a member."""
    book_id_str   = request.form.get("book_id",   "").strip()
    member_id_str = request.form.get("member_id", "").strip()

    # ── Validation ────────────────────────────────────────────
    if not book_id_str or not member_id_str or not book_id_str.isdigit() or not member_id_str.isdigit():
        flash("Please select both a valid book and a member.", "error")
        return redirect(url_for("issued"))

    book_id = int(book_id_str)
    member_id = int(member_id_str)

    conn = get_db()

    # Verify member exists
    member = conn.execute("SELECT id, name FROM members WHERE id = ?", (member_id,)).fetchone()
    if not member:
        conn.close()
        flash("Selected member was not found.", "error")
        return redirect(url_for("issued"))

    # Check the book exists and has copies available
    book = conn.execute(
        "SELECT id, title, available FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    if not book:
        conn.close()
        flash("Selected book was not found.", "error")
        return redirect(url_for("issued"))
    if book["available"] <= 0:
        conn.close()
        flash(f'No available copies of "{book["title"]}" left.', "error")
        return redirect(url_for("issued"))

    # Prevent issuing the same book to the same member again if still active
    duplicate = conn.execute("""
        SELECT id FROM issued_books
        WHERE book_id = ? AND member_id = ? AND status = 'issued'
    """, (book_id, member_id)).fetchone()
    if duplicate:
        conn.close()
        flash(f'"{member["name"]}" already has an active loan for "{book["title"]}".', "error")
        return redirect(url_for("issued"))

    today = date.today()
    due   = today + timedelta(days=14)

    # Record the loan
    conn.execute("""
        INSERT INTO issued_books (book_id, member_id, issue_date, due_date, status)
        VALUES (?, ?, ?, ?, 'issued')
    """, (book_id, member_id, today.isoformat(), due.isoformat()))

    # Decrease the available count by 1
    conn.execute(
        "UPDATE books SET available = available - 1 WHERE id = ? AND available > 0",
        (book_id,)
    )
    conn.commit()
    conn.close()

    flash(f'Book "{book["title"]}" issued to {member["name"]}. Due date: {due.strftime("%d %b %Y")}.', "success")
    return redirect(url_for("issued"))


@app.route("/issued/return/<int:issue_id>", methods=["POST"])
@login_required
def return_book(issue_id):
    """Mark a borrowed book as returned and compute any fine."""
    conn = get_db()
    loan = conn.execute(
        "SELECT * FROM issued_books WHERE id = ?", (issue_id,)
    ).fetchone()

    if not loan or loan["status"] == "returned":
        conn.close()
        flash("Invalid or already-returned loan.", "error")
        return redirect(url_for("issued"))

    today = date.today()
    try:
        due_date = date.fromisoformat(loan["due_date"])
    except (ValueError, TypeError):
        due_date = today

    fine = 0.0
    if today > due_date:
        overdue_days = (today - due_date).days
        fine = overdue_days * 5.0   # ₹5 per day late fine

    conn.execute("""
        UPDATE issued_books
        SET status = 'returned', return_date = ?, fine = ?
        WHERE id = ?
    """, (today.isoformat(), fine, issue_id))

    # Give the copy back — increase available count (never exceed total quantity)
    conn.execute("""
        UPDATE books
        SET available = MIN(available + 1, quantity)
        WHERE id = ?
    """, (loan["book_id"],))

    conn.commit()
    conn.close()

    if fine > 0:
        overdue_days = (today - due_date).days
        flash(f'Book returned. Late fine: ₹{fine:.0f} ({overdue_days} day(s) overdue).', "warning")
    else:
        flash("Book returned on time. No fine charged. ✅", "success")
    return redirect(url_for("issued"))


# ─── Start the App ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)
