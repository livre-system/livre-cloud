import os
import uuid
import boto3
from botocore.config import Config
from fastapi import UploadFile, HTTPException
from PIL import Image
import io

R2_ACCESS_KEY_ID     = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_ACCOUNT_ID        = os.environ["R2_ACCOUNT_ID"]
R2_BUCKET_NAME       = os.environ["R2_BUCKET_NAME"]
R2_PUBLIC_URL        = os.environ["R2_PUBLIC_URL"].rstrip("/")

s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    config=Config(signature_version="s3v4"),
    region_name="auto",
)

MAX_SIZE = 5 * 1024 * 1024  # 5MB

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
EXTENSIONS    = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

async def upload_image(file: UploadFile) -> str:
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Formato no permitido. Usá JPG, PNG, WEBP o GIF.")

    contents = await file.read()

    if len(contents) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="La imagen supera el límite de 5MB.")

    # Verificar que sea realmente una imagen
    try:
        img = Image.open(io.BytesIO(contents))
        img.verify()
    except Exception:
        raise HTTPException(status_code=400, detail="El archivo no es una imagen válida.")

    ext      = EXTENSIONS[file.content_type]
    filename = f"{uuid.uuid4().hex}{ext}"

    s3.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=filename,
        Body=contents,
        ContentType=file.content_type,
    )

    return f"{R2_PUBLIC_URL}/{filename}"
