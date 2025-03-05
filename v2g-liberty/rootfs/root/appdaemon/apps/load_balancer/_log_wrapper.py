import inspect


def get_class_method_logger(logger):
    def log(msg: str):
        info = inspect.stack()[1][0]
        the_class = info.f_locals["self"].__class__.__name__
        the_method = info.f_code.co_name
        line_no = info.f_lineno
        msg = f"[{the_class}.{the_method}:{line_no}] {msg}"
        print(msg)
        logger(msg)

    return log
