"""Fast parallel upload for Telegram using multiple connections"""
import asyncio
import hashlib
import math
import os
from typing import Optional, Tuple, BinaryIO

from telethon import utils, helpers, TelegramClient
from telethon.crypto import AuthKey
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import SaveFilePartRequest, SaveBigFilePartRequest, GetFileRequest
from telethon.tl.types import InputFileBig, InputFile, TypeInputFile, Document, InputDocumentFileLocation


class DownloadSender:
    """Handles downloading file parts through a single connection"""
    
    def __init__(self, client: TelegramClient, sender: MTProtoSender, file, offset: int, 
                 limit: int, stride: int, count: int):
        self.client = client
        self.sender = sender
        self.request = GetFileRequest(file, offset=offset, limit=limit)
        self.stride = stride
        self.remaining = count
    
    async def next(self) -> Optional[bytes]:
        if not self.remaining:
            return None
        result = await self.client._call(self.sender, self.request)
        self.remaining -= 1
        self.request.offset += self.stride
        return result.bytes
    
    def disconnect(self):
        return self.sender.disconnect()


class UploadSender:
    """Handles uploading file parts through a single connection"""
    
    def __init__(self, client: TelegramClient, sender: MTProtoSender, file_id: int, 
                 part_count: int, big: bool, index: int, stride: int, loop):
        self.client = client
        self.sender = sender
        self.part_count = part_count
        if big:
            self.request = SaveBigFilePartRequest(file_id, index, part_count, b"")
        else:
            self.request = SaveFilePartRequest(file_id, index, b"")
        self.stride = stride
        self.previous = None
        self.loop = loop
    
    async def next(self, data: bytes):
        if self.previous:
            await self.previous
        self.previous = self.loop.create_task(self._next(data))
    
    async def _next(self, data: bytes):
        self.request.bytes = data
        await self.client._call(self.sender, self.request)
        self.request.file_part += self.stride
    
    async def disconnect(self):
        if self.previous:
            await self.previous
        return await self.sender.disconnect()


class ParallelTransferrer:
    """Manages multiple parallel upload/download connections"""
    
    def __init__(self, client: TelegramClient, dc_id: Optional[int] = None):
        self.client = client
        self.loop = self.client.loop
        self.dc_id = dc_id or self.client.session.dc_id
        self.auth_key = (None if dc_id and self.client.session.dc_id != dc_id
                        else self.client.session.auth_key)
        self.senders = None
        self.upload_ticker = 0
    
    async def _cleanup(self):
        await asyncio.gather(*[sender.disconnect() for sender in self.senders])
        self.senders = None
    
    @staticmethod
    def _get_connection_count(file_size: int, max_count: int = 20, 
                             full_size: int = 100 * 1024 * 1024) -> int:
        """Calculate optimal number of connections based on file size"""
        # Use the provided max_count from config
        if file_size > full_size:
            return max_count
        return math.ceil((file_size / full_size) * max_count)
    
    async def _create_sender(self) -> MTProtoSender:
        dc = await self.client._get_dc(self.dc_id)
        sender = MTProtoSender(self.auth_key, loggers=self.client._log)
        await sender.connect(self.client._connection(
            dc.ip_address, dc.port, dc.id,
            loggers=self.client._log,
            proxy=self.client._proxy
        ))
        if not self.auth_key:
            auth = await self.client(ExportAuthorizationRequest(self.dc_id))
            self.client._init_request.query = ImportAuthorizationRequest(
                id=auth.id, bytes=auth.bytes
            )
            req = InvokeWithLayerRequest(LAYER, self.client._init_request)
            await sender.send(req)
            self.auth_key = sender.auth_key
        return sender
    
    async def _create_upload_sender(self, file_id: int, part_count: int, big: bool, 
                                   index: int, stride: int) -> UploadSender:
        return UploadSender(
            self.client, await self._create_sender(), file_id, part_count, 
            big, index, stride, loop=self.loop
        )
    
    async def _create_download_sender(self, file, index: int, part_size: int,
                                     stride: int, part_count: int) -> DownloadSender:
        return DownloadSender(
            self.client, await self._create_sender(), file, index * part_size,
            part_size, stride, part_count
        )
    
    async def _init_upload(self, connections: int, file_id: int, part_count: int, big: bool):
        self.senders = [
            await self._create_upload_sender(file_id, part_count, big, 0, connections),
            *await asyncio.gather(*[
                self._create_upload_sender(file_id, part_count, big, i, connections)
                for i in range(1, connections)
            ])
        ]
    
    async def _init_download(self, connections: int, file, part_count: int, part_size: int):
        minimum, remainder = divmod(part_count, connections)
        
        def get_part_count():
            nonlocal remainder
            if remainder > 0:
                remainder -= 1
                return minimum + 1
            return minimum
        
        self.senders = [
            await self._create_download_sender(file, 0, part_size, connections * part_size, get_part_count()),
            *await asyncio.gather(*[
                self._create_download_sender(file, i, part_size, connections * part_size, get_part_count())
                for i in range(1, connections)
            ])
        ]
    
    async def init_upload(self, file_id: int, file_size: int, 
                         part_size_kb: Optional[float] = None,
                         connection_count: Optional[int] = None) -> Tuple[int, int, bool]:
        connection_count = connection_count or self._get_connection_count(file_size)
        part_size = (part_size_kb or utils.get_appropriated_part_size(file_size)) * 1024
        part_count = (file_size + part_size - 1) // part_size
        is_large = file_size > 10 * 1024 * 1024
        await self._init_upload(connection_count, file_id, part_count, is_large)
        return part_size, part_count, is_large
    
    async def upload(self, part: bytes):
        await self.senders[self.upload_ticker].next(part)
        self.upload_ticker = (self.upload_ticker + 1) % len(self.senders)
    
    async def finish_upload(self):
        await self._cleanup()
    
    async def download(self, file, file_size: int, part_size_kb: Optional[float] = None,
                      connection_count: Optional[int] = None):
        """Download file using parallel connections"""
        connection_count = connection_count or self._get_connection_count(file_size)
        part_size = (part_size_kb or utils.get_appropriated_part_size(file_size)) * 1024
        part_count = math.ceil(file_size / part_size)
        
        await self._init_download(connection_count, file, part_count, part_size)
        
        part = 0
        while part < part_count:
            tasks = []
            for sender in self.senders:
                tasks.append(self.loop.create_task(sender.next()))
            for task in tasks:
                data = await task
                if not data:
                    break
                yield data
                part += 1
        
        await self._cleanup()


def stream_file(file_to_stream: BinaryIO, chunk_size=1024):
    """Stream file in chunks"""
    while True:
        data_read = file_to_stream.read(chunk_size)
        if not data_read:
            break
        yield data_read


async def parallel_upload_file(client: TelegramClient, file_path: str, 
                               progress_callback=None, max_connections: int = 20) -> Tuple[TypeInputFile, int]:
    """Upload file using parallel connections for maximum speed"""
    file_id = helpers.generate_random_long()
    file_size = os.path.getsize(file_path)
    
    hash_md5 = hashlib.md5()
    uploader = ParallelTransferrer(client)
    part_size, part_count, is_large = await uploader.init_upload(file_id, file_size, connection_count=max_connections)
    
    buffer = bytearray()
    with open(file_path, 'rb') as f:
        for data in stream_file(f, part_size):
            if progress_callback:
                progress_callback(f.tell(), file_size)
            
            if not is_large:
                hash_md5.update(data)
            
            if len(buffer) == 0 and len(data) == part_size:
                await uploader.upload(data)
                continue
            
            new_len = len(buffer) + len(data)
            if new_len >= part_size:
                cutoff = part_size - len(buffer)
                buffer.extend(data[:cutoff])
                await uploader.upload(bytes(buffer))
                buffer.clear()
                buffer.extend(data[cutoff:])
            else:
                buffer.extend(data)
        
        if len(buffer) > 0:
            await uploader.upload(bytes(buffer))
    
    await uploader.finish_upload()
    
    if is_large:
        return InputFileBig(file_id, part_count, os.path.basename(file_path)), file_size
    else:
        return InputFile(file_id, part_count, os.path.basename(file_path), 
                        hash_md5.hexdigest()), file_size


async def parallel_download_file(client: TelegramClient, message, output_path: str,
                                 progress_callback=None):
    """Download file using parallel connections for maximum speed"""
    file_size = message.file.size
    
    # Get file location
    dc_id, location = utils.get_input_location(message.document)
    
    downloader = ParallelTransferrer(client, dc_id)
    
    with open(output_path, 'wb') as f:
        async for chunk in downloader.download(location, file_size):
            f.write(chunk)
            if progress_callback:
                progress_callback(f.tell(), file_size)
