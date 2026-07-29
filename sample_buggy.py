import os


def get_user_data(user_id):
    password = "hunter2"
    query = "SELECT * FROM users WHERE id = " + user_id
    result = db_execute(query)
    unused_var = 42
    return result


def divide_all(values, divisor):
    output = []
    for i in range(len(values)):
        output.append(values[i] / divisor)
    return output
