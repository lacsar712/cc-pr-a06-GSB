import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, model_validator
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54394/printreg")
SECRET = os.environ.get("JWT_SECRET", "print-register-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "printer": {"role": "writer", "password_hash": pwd.hash("print123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}

DEFAULT_MIN_WEIGHT = 80.0
DEFAULT_MAX_WEIGHT = 120.0


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id serial PRIMARY KEY,
    sheet text NOT NULL,
    cyan_mm double precision NOT NULL,
    magenta_mm double precision NOT NULL,
    weight_gsm double precision NOT NULL,
    status text NOT NULL,
    verdict text NOT NULL DEFAULT '',
    reason text NOT NULL DEFAULT '',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS weight_settings (
    id integer PRIMARY KEY DEFAULT 1,
    min_gsm double precision NOT NULL,
    max_gsm double precision NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL,
    CONSTRAINT weight_settings_singleton CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS weight_history (
    id serial PRIMARY KEY,
    job_id integer NOT NULL REFERENCES jobs(id),
    event text NOT NULL,
    weight_gsm double precision NOT NULL,
    min_gsm double precision NOT NULL,
    max_gsm double precision NOT NULL,
    accepted boolean NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL
);
"""


class LoginIn(BaseModel):
    username: str
    password: str


class JobIn(BaseModel):
    sheet: str
    cyan_mm: float
    magenta_mm: float
    weight_gsm: float


class WeightSettingsIn(BaseModel):
    min_gsm: float
    max_gsm: float

    @model_validator(mode="after")
    def _check_range(self):
        if self.min_gsm <= 0 or self.max_gsm <= 0:
            raise ValueError("克重必须为正数")
        if self.min_gsm > self.max_gsm:
            raise ValueError("克重区间下限不能大于上限")
        return self


class WeightIn(BaseModel):
    weight_gsm: float

    @model_validator(mode="after")
    def _check_positive(self):
        if self.weight_gsm <= 0:
            raise ValueError("克重必须为正数")
        return self


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
        raise HTTPException(status_code=403, detail="仅印刷员可操作")
    return user


def get_settings(conn) -> dict:
    return conn.execute(
        "SELECT min_gsm, max_gsm, updated_by, updated_at FROM weight_settings WHERE id = 1"
    ).fetchone()


def in_range(weight: float, settings: dict) -> bool:
    # 闭区间：落在边界上也算合格
    return settings["min_gsm"] <= weight <= settings["max_gsm"]


app = FastAPI(title="印刷套准复核台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(SCHEMA)
        conn.commit()
        # 兼容旧库：jobs 缺 weight_gsm 列时补上（新库列已存在，用保存点吞掉重复列错误，
        # 不能直接 rollback，否则会回滚上面的建表 DDL）
        try:
            with conn.transaction():
                conn.execute("ALTER TABLE jobs ADD COLUMN weight_gsm double precision NOT NULL DEFAULT 0")
        except psycopg.errors.DuplicateColumn:
            pass
        conn.execute("ALTER TABLE jobs ALTER COLUMN weight_gsm DROP DEFAULT")
        conn.commit()
        if get_settings(conn) is None:
            conn.execute(
                """INSERT INTO weight_settings (id, min_gsm, max_gsm, updated_by, updated_at)
                   VALUES (1, %s, %s, 'printer', %s)""",
                (DEFAULT_MIN_WEIGHT, DEFAULT_MAX_WEIGHT, datetime.now(timezone.utc)),
            )
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            conn.execute(
                """INSERT INTO jobs
                       (sheet, cyan_mm, magenta_mm, weight_gsm, status, verdict, reason, created_by, created_at)
                   VALUES
                       ('封面-01', 0.05, -0.04, 100.0, 'pending', '', '', 'printer', %s),
                       ('内页-09', 0.40, 0.02, 90.0, 'pending', '', '', 'printer', %s)""",
                (now, now),
            )
            for sheet, weight in (("封面-01", 100.0), ("内页-09", 90.0)):
                conn.execute(
                    """INSERT INTO weight_history
                           (job_id, event, weight_gsm, min_gsm, max_gsm, accepted, created_by, created_at)
                       SELECT id, '送检', %s, %s, %s, true, 'printer', %s FROM jobs WHERE sheet = %s""",
                    (weight, DEFAULT_MIN_WEIGHT, DEFAULT_MAX_WEIGHT, now, sheet),
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
            """SELECT id, sheet, cyan_mm, magenta_mm, weight_gsm, status, verdict, reason, created_by
               FROM jobs ORDER BY id DESC"""
        ).fetchall()


@app.post("/api/jobs", status_code=202)
def enqueue(body: JobIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        settings = get_settings(conn)
        if not in_range(body.weight_gsm, settings):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"克重 {body.weight_gsm:g} 不在闭区间 "
                    f"[{settings['min_gsm']:g}, {settings['max_gsm']:g}] 内，退回"
                ),
            )
        now = datetime.now(timezone.utc)
        row = conn.execute(
            """INSERT INTO jobs
                   (sheet, cyan_mm, magenta_mm, weight_gsm, status, created_by, created_at)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s)
               RETURNING id, sheet, weight_gsm, status, verdict""",
            (body.sheet.strip(), body.cyan_mm, body.magenta_mm, body.weight_gsm, user["username"], now),
        ).fetchone()
        # 克重原文快照进履历，事后改行上克重也不会改动这笔数字
        conn.execute(
            """INSERT INTO weight_history
                   (job_id, event, weight_gsm, min_gsm, max_gsm, accepted, created_by, created_at)
               VALUES (%s, '送检', %s, %s, %s, true, %s, %s)""",
            (row["id"], body.weight_gsm, settings["min_gsm"], settings["max_gsm"], user["username"], now),
        )
        conn.commit()
    return row


@app.patch("/api/jobs/{job_id}/weight")
def update_weight(job_id: int, body: WeightIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        settings = get_settings(conn)
        if not in_range(body.weight_gsm, settings):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"克重 {body.weight_gsm:g} 不在闭区间 "
                    f"[{settings['min_gsm']:g}, {settings['max_gsm']:g}] 内，退回"
                ),
            )
        job = conn.execute("SELECT id FROM jobs WHERE id = %s", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="找不到该印张")
        now = datetime.now(timezone.utc)
        row = conn.execute(
            "UPDATE jobs SET weight_gsm = %s WHERE id = %s RETURNING id, sheet, weight_gsm, status, verdict",
            (body.weight_gsm, job_id),
        ).fetchone()
        # 只追加新一笔，早先履历保持原样
        conn.execute(
            """INSERT INTO weight_history
                   (job_id, event, weight_gsm, min_gsm, max_gsm, accepted, created_by, created_at)
               VALUES (%s, '改克重', %s, %s, %s, true, %s, %s)""",
            (job_id, body.weight_gsm, settings["min_gsm"], settings["max_gsm"], user["username"], now),
        )
        conn.commit()
    return row


@app.get("/api/weight/settings")
def read_settings(_user: dict = Depends(current_user)):
    with connect() as conn:
        return get_settings(conn)


@app.put("/api/weight/settings")
def write_settings(body: WeightSettingsIn, user: dict = Depends(require_writer)):
    with connect() as conn:
        now = datetime.now(timezone.utc)
        row = conn.execute(
            """INSERT INTO weight_settings (id, min_gsm, max_gsm, updated_by, updated_at)
                   VALUES (1, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE
                   SET min_gsm = EXCLUDED.min_gsm,
                       max_gsm = EXCLUDED.max_gsm,
                       updated_by = EXCLUDED.updated_by,
                       updated_at = EXCLUDED.updated_at
               RETURNING min_gsm, max_gsm, updated_by, updated_at""",
            (body.min_gsm, body.max_gsm, user["username"], now),
        ).fetchone()
        conn.commit()
    return row


@app.get("/api/weight/history")
def list_history(_user: dict = Depends(current_user)):
    with connect() as conn:
        return conn.execute(
            """SELECT h.id, h.job_id, j.sheet, h.event, h.weight_gsm, h.min_gsm, h.max_gsm,
                      h.accepted, h.created_by, h.created_at
               FROM weight_history h
               JOIN jobs j ON j.id = h.job_id
               ORDER BY h.id DESC"""
        ).fetchall()
