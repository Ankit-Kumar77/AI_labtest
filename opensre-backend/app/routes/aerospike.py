from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import aerospike

router = APIRouter(
    prefix="/api/aerospike",
    tags=["Aerospike"],
)


class WriteRequest(BaseModel):
    namespace: str
    set: str
    key: str
    bins: dict


class DeleteRequest(BaseModel):
    namespace: str
    set: str
    key: str


class ScanRequest(BaseModel):
    namespace: str
    set: str


@router.get("/health")
def health():
    return aerospike.health()


@router.get("/query")
def query_record(
    namespace: str = Query(...),
    set: str = Query(...),
    key: str = Query(...),
):
    return aerospike.query(namespace, set, key)


@router.post("/write")
def write_record(request: WriteRequest):
    return aerospike.write(request.namespace, request.set, request.key, request.bins)


@router.post("/delete")
def delete_record(request: DeleteRequest):
    return aerospike.delete(request.namespace, request.set, request.key)


@router.post("/scan")
def scan_records(request: ScanRequest):
    return aerospike.scan(request.namespace, request.set)