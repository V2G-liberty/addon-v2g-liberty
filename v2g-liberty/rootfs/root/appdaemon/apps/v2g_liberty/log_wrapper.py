import inspect


def get_class_method_logger(ad_log):
    def log(msg: str, level: str = ""):
        info = inspect.stack()[1][0]
        the_class = info.f_locals["self"].__class__.__name__[:20]
        the_method = info.f_code.co_name[:20]
        # The current custom is to add the method name to the message, this is not needed any more as
        # it is added here already. So, if it is in the message, remove it.
        msg = msg.replace(f"{the_method} ", "")
        line_no = info.f_lineno
        if level == "":
            level = "INFO"
        msg = f"{the_class} ({line_no}) > {the_method} > {msg}"
        ad_log(msg=msg, level=level)

    return log
