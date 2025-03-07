from module.DBConnection import DBConnectionPool, sql_execute


# 종목 테이블 #

async def get_stocks(pool: DBConnectionPool, name: str):
    query = "SELECT ST_CODE FROM KIS_LOCAL_STOCKS WHERE NAME = %s"
    return await sql_execute(pool, query, (name,))