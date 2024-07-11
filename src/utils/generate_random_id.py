import random
import string


def generate_random_id(length=8) -> str:
    characters = string.ascii_letters + string.digits
    random_id = "".join(random.choice(characters) for _ in range(length))
    return random_id
