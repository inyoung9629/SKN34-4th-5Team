from config.settings import *

# Never create/migrate the live application DB. This worker uses only a uniquely named local test DB.
DATABASES = {"default": {**DATABASES["default"], "HOST": "127.0.0.1", "PORT": "5432",
                         "NAME": "postgres", "TEST": {"NAME": "test_chat_attachments_ctx98496bac"}}}
