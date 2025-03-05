from http.client import HTTPException

import asyncmy

class DBConnectionPool:
    def __init__(self, max_size: int = 10):
        self.max_size = max_size
        self.pool = []  # 커넥션 풀을 저장하는 리스트

    async def get_connection(self):
        # 커넥션 풀에서 사용 가능한 커넥션을 반환하거나, 새로운 커넥션을 생성하여 반환
        if not self.pool:
            conn = await asyncmy.connect(
                host='localhost', user='root', password='qwer1234!', db='AUTO_TRADER'
            )
            return conn
        else:
            return self.pool.pop()

    async def release_connection(self, conn):
        # 풀에 커넥션을 반환하거나, 풀 사이즈가 최대에 달하면 커넥션을 닫음
        if len(self.pool) < self.max_size:
            self.pool.append(conn)
        else:
            await conn.close()


async def sql_execute(pool: DBConnectionPool, query: str, params: tuple = ()):
    """
    주어진 쿼리를 실행하고 결과를 반환합니다.
    :param pool: DBConnectionPool 객체
    :param query: 실행할 SQL 쿼리
    :param params: 쿼리에 전달할 매개변수
    :return: 쿼리 결과
    """
    conn = await pool.get_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(query, params)

            # SELECT 쿼리의 결과 처리
            if query.strip().lower().startswith("select"):
                rows = await cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]  # 열 이름
                result = [dict(zip(columns, row)) for row in rows]
                return result

            # INSERT, UPDATE, DELETE의 결과 처리
            affected_rows = cursor.rowcount
            if affected_rows == -1:
                # 영향을 받은 행이 없는 경우 추가 처리 (필요 시)
                await conn.rollback()
            await conn.commit()
            return affected_rows
    except Exception as e:
        # 예외 발생 시 롤백
        await conn.rollback()
        raise e

    finally:
        # 커넥션 반환 (예외 발생 여부와 관계없이)
        if conn:
            await pool.release_connection(conn)
