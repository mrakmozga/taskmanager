from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List
import models
import schemas
import auth
from database import get_db, engine, Base

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Task Manager API",
    description="""
## Трёхзвенная архитектура — Task Manager

Система управления задачами с ролевой моделью доступа.

### Роли пользователей:
- **admin** — полный доступ: управление пользователями, создание/редактирование/удаление любых задач
- **moderator** — создание задач, редактирование любых задач, назначение исполнителей
- **viewer** — просмотр задач, обновление статуса только своих назначенных задач

### Аутентификация:
Используется JWT Bearer токен. Получите токен через `/api/auth/login`, затем используйте его в заголовке `Authorization: Bearer <token>`.
    """,
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── AUTH ──────────────────────────────────────────────────────────────────────

@app.post("/api/auth/register", response_model=schemas.UserOut, status_code=201, tags=["Auth"],
          summary="Регистрация нового пользователя (роль viewer по умолчанию)")
def register(user_data: schemas.UserCreate, db: Session = Depends(get_db)):
    if db.query(models.User).filter(models.User.username == user_data.username).first():
        raise HTTPException(status_code=400, detail="Username already taken")
    if db.query(models.User).filter(models.User.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = models.User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=auth.get_password_hash(user_data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/api/auth/login", response_model=schemas.Token, tags=["Auth"],
          summary="Вход в систему, получение JWT токена")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not auth.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = auth.create_access_token({"sub": user.username})
    return {"access_token": token, "token_type": "bearer"}


@app.get("/api/auth/me", response_model=schemas.UserOut, tags=["Auth"],
         summary="Получить информацию о текущем пользователе")
def get_me(current_user: models.User = Depends(auth.get_current_user)):
    return current_user


# ── USERS (admin only) ────────────────────────────────────────────────────────

@app.get("/api/users", response_model=List[schemas.UserOut], tags=["Users"],
         summary="[admin, moderator] Список всех пользователей")
def list_users(
    db: Session = Depends(get_db),
    _: models.User = Depends(auth.require_role(models.RoleEnum.admin, models.RoleEnum.moderator))
):
    return db.query(models.User).all()


@app.get("/api/users/{user_id}", response_model=schemas.UserOut, tags=["Users"],
         summary="[admin] Получить пользователя по ID")
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(auth.require_role(models.RoleEnum.admin, models.RoleEnum.moderator))
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.patch("/api/users/{user_id}", response_model=schemas.UserOut, tags=["Users"],
           summary="[admin] Обновить пользователя (в т.ч. изменить роль)")
def update_user(
    user_id: int,
    data: schemas.UserUpdate,
    db: Session = Depends(get_db),
    _: models.User = Depends(auth.require_role(models.RoleEnum.admin))
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if data.email:
        user.email = data.email
    if data.role:
        user.role = data.role
    if data.password:
        user.hashed_password = auth.get_password_hash(data.password)
    db.commit()
    db.refresh(user)
    return user


@app.delete("/api/users/{user_id}", status_code=204, tags=["Users"],
            summary="[admin] Удалить пользователя")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.RoleEnum.admin))
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()


# ── TASKS ─────────────────────────────────────────────────────────────────────

@app.get("/api/tasks", response_model=List[schemas.TaskOut], tags=["Tasks"],
         summary="[all] Список задач (viewer видит только свои назначенные)")
def list_tasks(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    if current_user.role == models.RoleEnum.viewer:
        return db.query(models.Task).filter(models.Task.assignee_id == current_user.id).all()
    return db.query(models.Task).all()


@app.post("/api/tasks", response_model=schemas.TaskOut, status_code=201, tags=["Tasks"],
          summary="[admin, moderator] Создать задачу")
def create_task(
    data: schemas.TaskCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(models.RoleEnum.admin, models.RoleEnum.moderator))
):
    task = models.Task(
        title=data.title,
        description=data.description,
        owner_id=current_user.id,
        assignee_id=data.assignee_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@app.get("/api/tasks/{task_id}", response_model=schemas.TaskOut, tags=["Tasks"],
         summary="[all] Получить задачу по ID")
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if current_user.role == models.RoleEnum.viewer and task.assignee_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return task


@app.patch("/api/tasks/{task_id}", response_model=schemas.TaskOut, tags=["Tasks"],
           summary="[admin, moderator] Обновить задачу; [viewer] — только статус своей задачи")
def update_task(
    task_id: int,
    data: schemas.TaskUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if current_user.role == models.RoleEnum.viewer:
        if task.assignee_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        if data.title or data.description or data.assignee_id:
            raise HTTPException(status_code=403, detail="Viewers can only update task status")
        task.status = data.status
    else:
        if data.title:
            task.title = data.title
        if data.description is not None:
            task.description = data.description
        if data.status:
            task.status = data.status
        if data.assignee_id is not None:
            task.assignee_id = data.assignee_id

    db.commit()
    db.refresh(task)
    return task


@app.delete("/api/tasks/{task_id}", status_code=204, tags=["Tasks"],
            summary="[admin] Удалить задачу")
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: models.User = Depends(auth.require_role(models.RoleEnum.admin, models.RoleEnum.moderator))
):
    task = db.query(models.Task).filter(models.Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(task)
    db.commit()


# ── SEED (создать начальных пользователей) ────────────────────────────────────

@app.post("/api/seed", tags=["Dev"], summary="Создать тестовых пользователей (только если БД пуста)")
def seed(db: Session = Depends(get_db)):
    if db.query(models.User).count() > 0:
        raise HTTPException(status_code=400, detail="Database already seeded")
    users = [
        ("admin", "admin@example.com", "admin123", models.RoleEnum.admin),
        ("moderator", "mod@example.com", "mod123", models.RoleEnum.moderator),
        ("viewer", "viewer@example.com", "viewer123", models.RoleEnum.viewer),
    ]
    for username, email, password, role in users:
        db.add(models.User(
            username=username,
            email=email,
            hashed_password=auth.get_password_hash(password),
            role=role,
        ))
    db.commit()
    return {"message": "Seed users created", "users": [
        {"username": "admin", "password": "admin123", "role": "admin"},
        {"username": "moderator", "password": "mod123", "role": "moderator"},
        {"username": "viewer", "password": "viewer123", "role": "viewer"},
    ]}
