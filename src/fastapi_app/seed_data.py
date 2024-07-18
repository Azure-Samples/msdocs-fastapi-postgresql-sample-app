from sqlmodel import SQLModel

import models


def load_from_json():
    models.create_db_and_tables()

def drop_all():
    # Explicitly remove these tables first to avoid cascade errors
    SQLModel.metadata.remove(models.Restaurant.__table__)
    SQLModel.metadata.remove(models.Review.__table__)
    SQLModel.metadata.drop_all(models.engine)


if __name__ == "__main__":
    load_from_json()
