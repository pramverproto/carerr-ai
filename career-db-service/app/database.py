import aiomysql

_pool: aiomysql.Pool | None = None


async def init_pool(host: str, port: int, user: str, password: str, db: str,
                    minsize: int = 1, maxsize: int = 5) -> None:
    global _pool
    _pool = await aiomysql.create_pool(
        host=host, port=port,
        user=user, password=password, db=db,
        minsize=minsize, maxsize=maxsize,
        autocommit=True,
        charset="utf8mb4",
    )


async def close_pool() -> None:
    global _pool
    if _pool:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


def get_pool() -> aiomysql.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool
