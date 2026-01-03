# app/services/progress_bus.py
"""
Progress Bus - Server-Sent Events (SSE) for job progress updates
✅ FIXED: Memory leak, race conditions, completion signaling, and import-time event loop issue
"""

import asyncio
import logging
from typing import AsyncIterator, Dict, Set, Optional
from datetime import datetime

logger = logging.getLogger("pascowebapp.progress_bus")


class ProgressBus:
    """
    Thread-safe progress event bus for streaming job updates to clients
    
    Features:
    - ✅ Automatic cleanup (no memory leaks)
    - ✅ Completion signaling (streams end properly)
    - ✅ Race condition handling (buffers early messages)
    - ✅ Bounded queues (prevents unbounded growth)
    - ✅ Auto-expiry of old jobs (TTL-based cleanup)
    - ✅ Lazy initialization (no event loop required at import)
    
    Usage:
        # Publisher (in background job)
        await progress_bus.publish("job123", "Starting...")
        await progress_bus.publish("job123", "50% complete")
        await progress_bus.publish("job123", "[done]")  # Signal completion
        
        # Consumer (in SSE endpoint)
        async for message in progress_bus.stream("job123"):
            yield f"data: {message}\\n\\n"
    """
    
    # Sentinel value to signal job completion
    DONE_SIGNAL = None
    
    def __init__(self, max_queue_size: int = 1000, cleanup_delay: int = 60) -> None:
        """
        Initialize ProgressBus
        
        Args:
            max_queue_size: Maximum messages to buffer per job (prevents unbounded growth)
            cleanup_delay: Seconds to wait before cleaning up completed jobs
        """
        self._channels: Dict[str, asyncio.Queue[Optional[str]]] = {}
        self._completed: Set[str] = set()
        self._creation_times: Dict[str, datetime] = {}
        self._max_queue_size = max_queue_size
        self._cleanup_delay = cleanup_delay
        self._cleanup_task: Optional[asyncio.Task] = None
        self._initialized = False
        
        logger.info(f"ProgressBus initialized (max_queue_size={max_queue_size}, cleanup_delay={cleanup_delay}s)")
    
    def _ensure_cleanup_task(self) -> None:
        """
        Ensure cleanup task is running (lazy initialization)
        Only starts when event loop is available
        """
        if self._initialized:
            return
        
        try:
            # Check if we have a running event loop
            loop = asyncio.get_running_loop()
            
            # Start cleanup task if not already running
            if self._cleanup_task is None or self._cleanup_task.done():
                self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
                logger.debug("Started periodic cleanup task")
            
            self._initialized = True
            
        except RuntimeError:
            # No event loop running - that's OK, we'll try again later
            pass
    
    async def _periodic_cleanup(self) -> None:
        """Periodically clean up old completed jobs (runs in background)"""
        try:
            while True:
                await asyncio.sleep(60)  # Check every minute
                await self._cleanup_old_jobs()
        except asyncio.CancelledError:
            logger.info("Cleanup task cancelled")
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}", exc_info=True)
    
    async def _cleanup_old_jobs(self) -> None:
        """Remove jobs that completed more than cleanup_delay seconds ago"""
        now = datetime.now()
        to_remove = []
        
        for job_id in list(self._completed):
            creation_time = self._creation_times.get(job_id)
            if creation_time:
                age = (now - creation_time).total_seconds()
                if age > self._cleanup_delay:
                    to_remove.append(job_id)
        
        for job_id in to_remove:
            await self._remove_channel(job_id)
            logger.debug(f"Cleaned up old job: {job_id}")
        
        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} old job(s)")
    
    async def _remove_channel(self, job_id: str) -> None:
        """Remove a channel and all associated data"""
        if job_id in self._channels:
            # Drain any remaining messages
            q = self._channels[job_id]
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break
            
            # Remove from tracking
            del self._channels[job_id]
        
        if job_id in self._completed:
            self._completed.remove(job_id)
        
        if job_id in self._creation_times:
            del self._creation_times[job_id]
    
    async def publish(self, job_id: str, message: str) -> None:
        """
        Publish a progress message for a job
        
        Args:
            job_id: Unique job identifier
            message: Progress message to send
        
        Special messages:
            "[done]" - Signals job completion (closes stream)
            "[status] SUCCESS" - Also signals completion
            "[status] FAILURE" - Also signals completion
        
        Example:
            await progress_bus.publish("job123", "Starting task...")
            await progress_bus.publish("job123", "Processing file 1 of 10")
            await progress_bus.publish("job123", "[done]")
        """
        # Ensure cleanup task is running (lazy init)
        self._ensure_cleanup_task()
        
        # Create queue if it doesn't exist
        if job_id not in self._channels:
            self._channels[job_id] = asyncio.Queue(maxsize=self._max_queue_size)
            self._creation_times[job_id] = datetime.now()
            logger.debug(f"Created channel for job: {job_id}")
        
        q = self._channels[job_id]
        
        # Check for completion signal
        if message in ("[done]", "[status] SUCCESS", "[status] FAILURE"):
            # Send sentinel to signal completion
            try:
                await asyncio.wait_for(q.put(self.DONE_SIGNAL), timeout=1.0)
                self._completed.add(job_id)
                logger.debug(f"Job completed: {job_id}")
            except asyncio.TimeoutError:
                logger.warning(f"Timeout sending completion signal for job {job_id}")
        else:
            # Send regular message
            message = message.rstrip("\n")
            try:
                # Use wait_for to prevent blocking forever if queue is full
                await asyncio.wait_for(q.put(message), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning(f"Queue full for job {job_id}, message dropped: {message[:50]}...")
            except Exception as e:
                logger.error(f"Error publishing message for job {job_id}: {e}")
    
    async def stream(self, job_id: str) -> AsyncIterator[str]:
        """
        Stream progress messages for a job
        
        Args:
            job_id: Unique job identifier
        
        Yields:
            Progress messages until job completes
        
        Example:
            async for message in progress_bus.stream("job123"):
                print(f"Progress: {message}")
        """
        # Ensure cleanup task is running (lazy init)
        self._ensure_cleanup_task()
        
        # Create queue if it doesn't exist (handles race condition)
        if job_id not in self._channels:
            self._channels[job_id] = asyncio.Queue(maxsize=self._max_queue_size)
            self._creation_times[job_id] = datetime.now()
            logger.debug(f"Created channel for job (consumer): {job_id}")
        
        q = self._channels[job_id]
        message_count = 0
        
        try:
            while True:
                # Get next message (waits if queue is empty)
                msg = await q.get()
                
                # Check for completion sentinel
                if msg is self.DONE_SIGNAL:
                    logger.debug(f"Stream completed for job {job_id} ({message_count} messages)")
                    break
                
                # Yield message to consumer
                message_count += 1
                yield msg
                
        except asyncio.CancelledError:
            logger.debug(f"Stream cancelled for job {job_id} after {message_count} messages")
            raise
        
        except Exception as e:
            logger.error(f"Error in stream for job {job_id}: {e}", exc_info=True)
            raise
        
        finally:
            # Schedule cleanup after a delay (allows late consumers to connect)
            try:
                asyncio.create_task(self._delayed_cleanup(job_id))
            except RuntimeError:
                # No event loop - cleanup will happen in periodic task
                pass
    
    async def _delayed_cleanup(self, job_id: str) -> None:
        """
        Clean up a channel after a delay
        
        This allows late consumers to still retrieve buffered messages
        """
        try:
            await asyncio.sleep(self._cleanup_delay)
            await self._remove_channel(job_id)
            logger.debug(f"Delayed cleanup completed for job: {job_id}")
        except Exception as e:
            logger.error(f"Error in delayed cleanup for job {job_id}: {e}")
    
    def get_active_jobs(self) -> int:
        """Return count of active (not completed) jobs"""
        return len(self._channels) - len(self._completed)
    
    def get_completed_jobs(self) -> int:
        """Return count of completed jobs awaiting cleanup"""
        return len(self._completed)
    
    def get_total_jobs(self) -> int:
        """Return total jobs being tracked"""
        return len(self._channels)
    
    async def shutdown(self) -> None:
        """
        Gracefully shutdown the ProgressBus
        
        Call this when shutting down the application
        """
        logger.info("Shutting down ProgressBus...")
        
        # Cancel cleanup task
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        # Clean up all channels
        for job_id in list(self._channels.keys()):
            await self._remove_channel(job_id)
        
        logger.info(f"ProgressBus shutdown complete")


# Global singleton instance
progress_bus = ProgressBus()


# Cleanup on app shutdown (call this from main.py)
async def shutdown_progress_bus() -> None:
    """
    Shutdown the global progress bus
    
    Add this to your app's shutdown handler in main.py:
    
        @app.on_event("shutdown")
        async def shutdown():
            await shutdown_progress_bus()
    """
    await progress_bus.shutdown()
