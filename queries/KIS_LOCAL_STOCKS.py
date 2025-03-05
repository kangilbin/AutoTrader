from module.DBConnection import DBConnectionPool, sql_execute


async def get_stocks(pool: DBConnectionPool, name: str):
    query = "SELECT ST_CODE FROM KIS_LOCAL_STOCKS WHERE NAME = %s"
    return await sql_execute(pool, query, (name,))