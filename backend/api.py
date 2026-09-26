import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54394/printreg")
SECRET = os.environ.get("JWT_SECRET", "print-register-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "printer": {"role": "writer", "password_hash": pwd.hash("print123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id serial PRIMARY KEY,
    sheet text NOT NULL,
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    status text NOT NULL,
    verdict text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS grammage_settings (
    id smallint PRIMARY KEY,
    min_grammage double precision NOT NULL,
    max_grammage double precision NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS grammage_submissions (
    id serial PRIMARY KEY,
    sheet text NOT NULL,
    grammage double precision NOT NULL,
    status text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS grammage_history (
    id serial PRIMARY KEY,
    submission_id integer NOT NULL REFERENCES grammage_submissions(id),
    sheet text NOT NULL,
    grammage double precision NOT NULL,
    status text NOT NULL,
    event text NOT NULL,
    recorded_by text NOT NULL,
    recorded_at timestamptz NOT NULL
);
"""


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    sheet: str
    cyan_mm: float
    magenta_mm: float


class IntervalIn(BaseModel):
    min_grammage: float
    max_grammage: float


class GrammageIn(BaseModel):
    sheet: str
    grammage: float


class GrammageEditIn(BaseModel):
    grammage: float


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅印刷员可送复核")
    return user


def writer_only(detail: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] != "writer":
            raise HTTPException(status_code=403, detail=detail)
        return user

    return dep


app = FastAPI(title="印刷套准复核台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(SCHEMA)
        now = datetime.now(timezone.utc)
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            conn.execute(
                """INSERT INTO jobs (sheet, cyan_mm, magenta_mm, status, verdict, reason, created_by, created_at)
                   VALUES
                   ('封面-01', 0.05, -0.04, 'pending', '', '', 'printer', %s),
                   ('内页-09', 0.40, 0.02, 'pending', '', '', 'printer', %s)""",
                (now, now),
            )
        m = conn.execute("SELECT COUNT(*) AS n FROM grammage_settings").fetchone()["n"]
        if m == 0:
            conn.execute(
                """INSERT INTO grammage_settings (id, min_grammage, max_grammage, updated_by, updated_at)
                   VALUES (1, 80, 120, 'system', %s)""",
                (now,),
            )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "print-register-review"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/jobs")
def list_jobs(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            "SELECT id, sheet, cyan_mm, magenta_mm, status, verdict, reason, created_by FROM jobs ORDER BY id DESC"
        ).fetchall()


@app.post("/api/jobs", status_code=202)
def enqueue(body: JobIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO jobs (sheet, cyan_mm, magenta_mm, status, created_by, created_at)
               VALUES (%s, %s, %s, 'pending', %s, %s)
               RETURNING id, sheet, status, verdict""",
            (body.sheet.strip(), body.cyan_mm, body.magenta_mm, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        conn.commit()
    return row


def get_interval(conn) -> dict:
    return conn.execute(
        "SELECT min_grammage, max_grammage, updated_by, updated_at FROM grammage_settings WHERE id = 1"
    ).fetchone()


def grammage_status(interval: dict, grammage: float) -> str:
    if interval["min_grammage"] <= grammage <= interval["max_grammage"]:
        return "accepted"
    return "rejected"


@app.get("/api/grammage/interval")
def read_interval(_user: dict = Depends(current_user)):
    with connect() as conn:
        return get_interval(conn)


@app.put("/api/grammage/interval")
def update_interval(body: IntervalIn, user: dict = Depends(writer_only("仅印刷员可改克重区间"))):
    if body.min_grammage <= 0 or body.max_grammage <= 0 or body.min_grammage > body.max_grammage:
        raise HTTPException(status_code=400, detail="区间无效：需 0 < 下限 ≤ 上限")
    with connect() as conn:
        conn.execute(
            """UPDATE grammage_settings
               SET min_grammage = %s, max_grammage = %s, updated_by = %s, updated_at = %s
               WHERE id = 1""",
            (body.min_grammage, body.max_grammage, user["username"], datetime.now(timezone.utc)),
        )
        conn.commit()
        return get_interval(conn)


@app.get("/api/grammage/submissions")
def list_submissions(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            """SELECT id, sheet, grammage, status, created_by, created_at, updated_at
               FROM grammage_submissions ORDER BY id DESC"""
        ).fetchall()


@app.post("/api/grammage/submissions", status_code=201)
def submit_grammage(body: GrammageIn, user: dict = Depends(writer_only("仅印刷员可投递"))):
    sheet = body.sheet.strip()
    if not sheet:
        raise HTTPException(status_code=400, detail="必须声明纸型")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        interval = get_interval(conn)
        status = grammage_status(interval, body.grammage)
        row = conn.execute(
            """INSERT INTO grammage_submissions (sheet, grammage, status, created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id, sheet, grammage, status, created_by, created_at, updated_at""",
            (sheet, body.grammage, status, user["username"], now, now),
        ).fetchone()
        conn.execute(
            """INSERT INTO grammage_history (submission_id, sheet, grammage, status, event, recorded_by, recorded_at)
               VALUES (%s, %s, %s, %s, 'submit', %s, %s)""",
            (row["id"], sheet, body.grammage, status, user["username"], now),
        )
        conn.commit()
    return row


@app.put("/api/grammage/submissions/{submission_id}")
def edit_grammage(submission_id: int, body: GrammageEditIn, user: dict = Depends(writer_only("仅印刷员可改克重"))):
    now = datetime.now(timezone.utc)
    with connect() as conn:
        current = conn.execute(
            "SELECT id, sheet FROM grammage_submissions WHERE id = %s", (submission_id,)
        ).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="送检记录不存在")
        interval = get_interval(conn)
        status = grammage_status(interval, body.grammage)
        row = conn.execute(
            """UPDATE grammage_submissions
               SET grammage = %s, status = %s, updated_at = %s
               WHERE id = %s
               RETURNING id, sheet, grammage, status, created_by, created_at, updated_at""",
            (body.grammage, status, now, submission_id),
        ).fetchone()
        conn.execute(
            """INSERT INTO grammage_history (submission_id, sheet, grammage, status, event, recorded_by, recorded_at)
               VALUES (%s, %s, %s, %s, 'edit', %s, %s)""",
            (submission_id, current["sheet"], body.grammage, status, user["username"], now),
        )
        conn.commit()
    return row


@app.get("/api/grammage/history")
def list_history(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            """SELECT id, submission_id, sheet, grammage, status, event, recorded_by, recorded_at
               FROM grammage_history ORDER BY id DESC"""
        ).fetchall()
