"""
S3-compatible storage client for bundeswarehouse.

Reads configuration from environment variables:
  S3_ENDPOINT_URL       - e.g. http://127.0.0.1:9000
  S3_ACCESS_KEY_ID      - access key
  S3_SECRET_ACCESS_KEY  - secret key
  S3_BUCKET             - bucket name (default: bundeswarehouse)
  S3_REGION             - region (default: us-east-1)
"""

import io
import logging
import os
import time
from typing import List, Optional

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

logger = logging.getLogger(__name__)

# Storage layout prefixes
PREFIX_RAW = "raw/"
PREFIX_STAGING = "raw/_staging/"
PREFIX_CURRENT = "raw/current/"
PREFIX_PROCESSED = "processed/"
PREFIX_MANIFESTS = "manifests/"


def get_s3_client():
    """Create and return a boto3 S3 client using environment variables."""
    endpoint_url = os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:9000")
    access_key = os.environ.get("S3_ACCESS_KEY_ID")
    secret_key = os.environ.get("S3_SECRET_ACCESS_KEY")
    region = os.environ.get("S3_REGION", "us-east-1")

    if not access_key or not secret_key:
        raise EnvironmentError(
            "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY environment variables must be set."
        )

    client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )
    return client


def get_bucket_name() -> str:
    """Return the configured S3 bucket name."""
    return os.environ.get("S3_BUCKET", "bundeswarehouse")


def ensure_bucket_exists(client, bucket: str) -> None:
    """Create the bucket if it does not already exist."""
    try:
        client.head_bucket(Bucket=bucket)
        logger.debug("Bucket '%s' already exists.", bucket)
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code in ("404", "NoSuchBucket"):
            logger.info("Creating bucket '%s'.", bucket)
            client.create_bucket(Bucket=bucket)
        else:
            raise


def upload_bytes(
    client,
    bucket: str,
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    retries: int = 3,
    backoff: float = 2.0,
) -> None:
    """Upload raw bytes to S3 with retry logic."""
    for attempt in range(1, retries + 1):
        try:
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
            logger.info("Uploaded %d bytes → s3://%s/%s", len(data), bucket, key)
            return
        except (ClientError, EndpointConnectionError) as exc:
            if attempt == retries:
                raise
            wait = backoff ** attempt
            logger.warning(
                "Upload attempt %d/%d failed (%s). Retrying in %.1fs.",
                attempt,
                retries,
                exc,
                wait,
            )
            time.sleep(wait)


def upload_file(
    client,
    bucket: str,
    key: str,
    local_path: str,
    content_type: str = "application/octet-stream",
    retries: int = 3,
    backoff: float = 2.0,
) -> None:
    """Upload a local file to S3 with retry logic (streaming)."""
    for attempt in range(1, retries + 1):
        try:
            with open(local_path, "rb") as fh:
                client.upload_fileobj(
                    fh,
                    bucket,
                    key,
                    ExtraArgs={"ContentType": content_type},
                )
            logger.info("Uploaded file '%s' → s3://%s/%s", local_path, bucket, key)
            return
        except (ClientError, EndpointConnectionError) as exc:
            if attempt == retries:
                raise
            wait = backoff ** attempt
            logger.warning(
                "Upload attempt %d/%d failed (%s). Retrying in %.1fs.",
                attempt,
                retries,
                exc,
                wait,
            )
            time.sleep(wait)


def download_file(
    client,
    bucket: str,
    key: str,
    local_path: str,
    retries: int = 3,
    backoff: float = 2.0,
) -> bool:
    """Download an S3 object to a local file (streaming). Returns False if the key does not exist."""
    for attempt in range(1, retries + 1):
        try:
            with open(local_path, "wb") as fh:
                client.download_fileobj(bucket, key, fh)
            logger.info("Downloaded s3://%s/%s → '%s'", bucket, key, local_path)
            return True
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code in ("404", "NoSuchKey"):
                # Remove the empty file created by open() above so callers
                # don't encounter a zero-byte file masquerading as a valid db.
                try:
                    os.remove(local_path)
                except FileNotFoundError:
                    pass
                return False
            if attempt == retries:
                raise
            wait = backoff ** attempt
            logger.warning(
                "Download attempt %d/%d failed (%s). Retrying in %.1fs.",
                attempt,
                retries,
                exc,
                wait,
            )
            time.sleep(wait)
        except EndpointConnectionError as exc:
            if attempt == retries:
                raise
            wait = backoff ** attempt
            logger.warning(
                "Download attempt %d/%d failed (%s). Retrying in %.1fs.",
                attempt,
                retries,
                exc,
                wait,
            )
            time.sleep(wait)
    return False


def download_bytes(
    client,
    bucket: str,
    key: str,
    retries: int = 3,
    backoff: float = 2.0,
) -> Optional[bytes]:
    """Download an S3 object as bytes. Returns None if the key does not exist."""
    for attempt in range(1, retries + 1):
        try:
            response = client.get_object(Bucket=bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code in ("404", "NoSuchKey"):
                return None
            if attempt == retries:
                raise
            wait = backoff ** attempt
            logger.warning(
                "Download attempt %d/%d failed (%s). Retrying in %.1fs.",
                attempt,
                retries,
                exc,
                wait,
            )
            time.sleep(wait)
        except EndpointConnectionError as exc:
            if attempt == retries:
                raise
            wait = backoff ** attempt
            logger.warning(
                "Download attempt %d/%d failed (%s). Retrying in %.1fs.",
                attempt,
                retries,
                exc,
                wait,
            )
            time.sleep(wait)
    return None


def check_connection(client, bucket: str) -> bool:
    """Smoke-test the S3 connection and bucket access. Returns True on success."""
    try:
        client.head_bucket(Bucket=bucket)
        logger.info("S3 connection OK – bucket '%s' is accessible.", bucket)
        return True
    except ClientError as exc:
        logger.error("S3 connection check failed: %s", exc)
        return False
    except EndpointConnectionError as exc:
        logger.error("S3 endpoint not reachable: %s", exc)
        return False


def list_prefix(client, bucket: str, prefix: str) -> List[str]:
    """Return the keys of all objects whose key starts with *prefix*."""
    keys: List[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def delete_prefix(client, bucket: str, prefix: str) -> int:
    """Delete every object whose key starts with *prefix*.

    Objects are deleted in batches of 1000 (the S3 API maximum).
    Returns the total number of objects deleted.
    """
    keys = list_prefix(client, bucket, prefix)
    if not keys:
        logger.debug("delete_prefix: no objects found under %r, nothing to delete.", prefix)
        return 0

    count = 0
    for i in range(0, len(keys), 1000):
        batch = [{"Key": k} for k in keys[i : i + 1000]]
        client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
        count += len(batch)

    logger.info("Deleted %d objects under prefix %r.", count, prefix)
    return count


def copy_object(client, bucket: str, src_key: str, dst_key: str) -> None:
    """Copy a single object within the same bucket."""
    client.copy_object(
        Bucket=bucket,
        CopySource={"Bucket": bucket, "Key": src_key},
        Key=dst_key,
    )
    logger.debug("Copied s3://%s/%s → s3://%s/%s", bucket, src_key, bucket, dst_key)
