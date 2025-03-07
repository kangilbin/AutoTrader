from model.SignupModel import Signup
from module.DBConnection import DBConnectionPool, sql_execute


async def user_signup(pool: DBConnectionPool, user: Signup):
    query = "INSERT INTO ACCOUNT (ID, PASSWORD, DEVICE_ID, API_KEY, SERCRET_KEY) VALUES (%s, %s, %s, %s, %s)"
    return await sql_execute(pool, query, (user.ID, user.PASSWORD, user.DEVICE_ID, user.API_KEY, user.SECRET_KEY))


async def user_login(pool: DBConnectionPool, user_id: str, user_pw: str):
    query = "SELECT * FROM ACCOUNT WHERE ID = %s AND PASSWORD = %s"
    return await sql_execute(pool, query, (user_id, user_pw))