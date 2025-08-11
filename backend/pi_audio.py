#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-CubeAlarm-Custom-Attribution
# Copyright (c) 2025 Paul Shapiro
"""
Raspberry Pi Audio Handler for Alarm Clock
Handles local audio playback on Raspberry Pi with fallback options.
"""

import os
import subprocess
import threading
import time
import logging
import re
from typing import Optional, Dict, Tuple
import platform

logger = logging.getLogger(__name__)

class PiAudioManager:
    """Manages audio playback on Raspberry Pi with multiple fallback options."""
    
    def __init__(self):
        self.active_alarms: Dict[str, threading.Thread] = {}
        self.active_processes: Dict[str, subprocess.Popen] = {}
        self.stop_events: Dict[str, threading.Event] = {}  # alarm_id -> subprocess.Popen object
        self.is_pi = self._detect_raspberry_pi()
        self.audio_method = self._detect_audio_method()

        if self.is_pi:
            self.audio_method = 'aplay'

        # Detect analog ALSA device (card and device numbers) so we can force
        # playback through the headphone jack instead of HDMI.  If detection
        # fails, fall back to card 0, device 0.
        self.alsa_card, self.alsa_device = self._detect_alsa_device()
        self.alsa_output = f"plughw:{self.alsa_card},{self.alsa_device}"

        logger.info(
            f"🔊 Audio Manager initialized - Platform: {'Pi' if self.is_pi else platform.system()}, Method: {self.audio_method}, ALSA output: {self.alsa_output}"
        )
    
    def _detect_raspberry_pi(self) -> bool:
        """Detect if running on Raspberry Pi."""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                return 'Raspberry Pi' in f.read()
        except:
            return False
    
    def _detect_audio_method(self) -> str:
        """Detect best available audio method."""
        methods = []
        
        # Check for pygame (preferred for Pi)
        try:
            import pygame
            methods.append('pygame')
        except ImportError:
            pass
        
        # Check for system audio commands
        if self._command_exists('aplay'):
            methods.append('aplay')
        if self._command_exists('paplay'):
            methods.append('paplay')
        if self._command_exists('speaker-test'):
            methods.append('speaker-test')
        if self._command_exists('afplay'):  # macOS
            methods.append('afplay')
        
        # Return best method
        if 'pygame' in methods:
            return 'pygame'
        elif 'aplay' in methods:
            return 'aplay'
        elif 'paplay' in methods:
            return 'paplay'
        elif 'afplay' in methods:
            return 'afplay'
        elif 'speaker-test' in methods:
            return 'speaker-test'
        else:
            return 'none'
    
    def _command_exists(self, command: str) -> bool:
        """Check if a command exists in PATH."""
        try:
            # Use shutil.which instead of subprocess which command
            # This works in systemd environments where 'which' may not be available
            import shutil
            return shutil.which(command) is not None
        except Exception:
            return False

    def _detect_alsa_device(self) -> Tuple[str, str]:
        """Detect the ALSA card/device numbers for the analog headphone jack.

        Returns (card, device) as strings. Defaults to ('0', '0') if detection
        fails or if aplay is unavailable.
        """
        try:
            output = subprocess.check_output(['aplay', '-l'], text=True)
            for line in output.splitlines():
                # Look for a device that represents the headphone jack.  On
                # modern Raspberry Pi systems this typically contains the word
                # "Headphones".
                if 'Headphones' in line or 'bcm2835' in line:
                    match = re.search(r'card (\d+):.*device (\d+):', line)
                    if match:
                        return match.group(1), match.group(2)
        except Exception as e:
            logger.debug(f"ALSA device detection failed: {e}")
        return '0', '0'
    
    def play_alarm_sound(self, alarm_id: str, sound_file: str = None) -> bool:
        """Play alarm sound continuously until stopped."""
        logger.info(f"🔊 DEBUG: play_alarm_sound called for ID: {alarm_id}")
        
        if alarm_id in self.active_alarms:
            logger.info(f"🔊 Alarm {alarm_id} already playing")
            return True
            
        # Resolve the alarm sound path relative to the project root so that
        # systemd or other launch methods using a different working directory
        # can still locate the audio file reliably.
        if sound_file:
            sound_path = sound_file
        else:
            env_path = os.environ.get('ALARM_SOUND_FILE')
            if env_path:
                sound_path = env_path
            else:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                sound_path = os.path.join(base_dir, 'sounds', 'alarm.wav')
        logger.info(f"🔊 Starting alarm sound for ID: {alarm_id} with file: {sound_path}")
        logger.info(f"🔊 DEBUG: Sound file exists: {os.path.exists(sound_path)}")
        logger.info(f"🔊 DEBUG: Current working directory: {os.getcwd()}")
        logger.info(f"🔊 DEBUG: Environment variables: PULSE_RUNTIME_PATH={os.environ.get('PULSE_RUNTIME_PATH')}, XDG_RUNTIME_DIR={os.environ.get('XDG_RUNTIME_DIR')}")

        # Re-detect ALSA device on each playback in case the system booted
        # before audio devices were fully initialized (common after power
        # cycles).  This ensures we always target the actual analog output
        # rather than falling back to the HDMI default.
        self.alsa_card, self.alsa_device = self._detect_alsa_device()
        self.alsa_output = f"plughw:{self.alsa_card},{self.alsa_device}"
        logger.info(f"🔊 DEBUG: Using ALSA output {self.alsa_output}")
        
        # Create stop event for this alarm
        stop_event = threading.Event()
        self.stop_events[alarm_id] = stop_event
        
        # Create and start alarm thread
        alarm_thread = threading.Thread(
            target=self._alarm_sound_loop,
            args=(alarm_id, alarm_id, sound_path, stop_event),
            daemon=True
        )
        alarm_thread.start()
        
        self.active_alarms[alarm_id] = alarm_thread
        return True

    # Backwards compatible wrapper expected by older interfaces
    def start_alarm_sound(self, alarm_id: str, alarm_label: str = "Alarm") -> bool:
        """Public API used by helper functions and tests."""
        return self.play_alarm_sound(alarm_id)
    
    def stop_alarm_sound(self, alarm_id: str) -> bool:
        """Stop playing alarm sound for given alarm ID."""
        logger.info(f"🔇 Stopping alarm sound for: {alarm_id}")
        
        # Check if we have an active process (even if thread has exited)
        has_active_process = alarm_id in self.active_processes
        has_active_thread = alarm_id in self.active_alarms
        
        if not has_active_process and not has_active_thread:
            logger.warning(f"⚠️ No alarm sound or process found for {alarm_id}")
            return False
        
        # Set stop event to signal the audio loop to stop
        if alarm_id in self.stop_events:
            self.stop_events[alarm_id].set()
        
        # Kill any active subprocess for this alarm
        if alarm_id in self.active_processes:
            process = self.active_processes[alarm_id]
            try:
                logger.info(f"🔇 DEBUG: Terminating process {process.pid} for {alarm_id}")
                # Kill the entire process group to ensure shell and child processes are terminated
                import signal
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    logger.info(f"🔇 DEBUG: Sent SIGTERM to process group {process.pid}")
                except ProcessLookupError:
                    logger.info(f"🔇 DEBUG: Process group {process.pid} already terminated")
                
                # Wait for graceful termination
                try:
                    process.wait(timeout=2)
                    logger.info(f"🔇 DEBUG: Process {process.pid} terminated gracefully")
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't terminate gracefully
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        logger.info(f"🔇 DEBUG: Force killed process group {process.pid}")
                    except ProcessLookupError:
                        logger.info(f"🔇 DEBUG: Process group {process.pid} already gone")
                    process.wait()
            except Exception as e:
                logger.error(f"❌ Error terminating process for {alarm_id}: {e}")
            finally:
                self.active_processes.pop(alarm_id, None)
        
        # Remove from active alarms and wait for thread to finish
        thread = self.active_alarms.pop(alarm_id, None)
        if thread and thread.is_alive():
            # Thread should exit naturally when stop event is set
            thread.join(timeout=3)  # Wait up to 3 seconds for thread to finish
            if thread.is_alive():
                logger.warning(f"⚠️ Alarm thread for {alarm_id} did not finish gracefully")
        
        # Clean up stop event
        self.stop_events.pop(alarm_id, None)
        
        return True
    
    def stop_all_alarms(self):
        """Stop all active alarm sounds."""
        logger.info("🔇 Stopping all alarm sounds")
        alarm_ids = list(self.active_alarms.keys())
        for alarm_id in alarm_ids:
            self.stop_alarm_sound(alarm_id)
    
    def _alarm_sound_loop(self, alarm_id: str, alarm_label: str, sound_path: str, stop_event: threading.Event):
        """Main alarm sound loop - runs until alarm is stopped."""
        logger.info(f"🎵 Starting alarm sound loop for: {alarm_label}")
        
        try:
            # Start the continuous audio playback
            success = self._play_alarm_sound_once(sound_path, alarm_id)
            if not success:
                logger.error(f"❌ Failed to start alarm sound for {alarm_label}")
                return
            
            # Wait for stop event while audio plays continuously
            while not stop_event.is_set():
                time.sleep(0.1)  # Check for stop every 0.1s
        
        except Exception as e:
            logger.error(f"❌ Error in alarm sound loop for {alarm_label}: {e}")
        
        finally:
            # Don't clean up process reference here - let stop_alarm_sound handle it
            # This prevents race condition where stop_alarm_sound can't find the process
            logger.info(f"🔇 Alarm sound loop ended for: {alarm_label}")
    
    def _play_alarm_sound_once(self, sound_file: str, alarm_id: str = None) -> bool:
        """Play alarm sound once using the best available method."""
        try:
            if self.audio_method == 'pygame':
                return self._play_pygame(sound_file)
            elif self.audio_method == 'aplay':
                return self._play_aplay(sound_file, alarm_id)
            elif self.audio_method == 'paplay':
                return self._play_paplay(sound_file)
            elif self.audio_method == 'afplay':
                return self._play_afplay(sound_file)
            elif self.audio_method == 'speaker-test':
                return self._play_speaker_test(sound_file, alarm_id)
            else:
                logger.error("❌ No audio method available")
                return self._play_console_beep()
        
        except Exception as e:
            logger.error(f"❌ Error playing alarm sound: {e}")
            return False
    
    def _play_pygame(self, sound_file: str) -> bool:
        """Play alarm sound using pygame."""
        try:
            import pygame
            
            # Initialize pygame mixer if not already done
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
            
            # Check for custom alarm sound file
            if os.path.exists(sound_file):
                sound = pygame.mixer.Sound(sound_file)
                sound.play()
                # Wait for sound to finish
                while pygame.mixer.get_busy():
                    time.sleep(0.1)
            else:
                # Generate beep sound programmatically
                self._generate_beep_pygame()
            
            return True
        
        except Exception as e:
            logger.error(f"❌ Pygame audio error: {e}")
            return False
    
    def _generate_beep_pygame(self):
        """Generate a beep sound using pygame."""
        try:
            import pygame
            import numpy as np
            
            # Generate a 800Hz sine wave for 1 second
            sample_rate = 22050
            duration = 1.0
            frequency = 800
            
            frames = int(duration * sample_rate)
            arr = np.zeros((frames, 2), dtype=np.int16)
            
            for i in range(frames):
                wave = int(16383 * np.sin(2 * np.pi * frequency * i / sample_rate))
                arr[i][0] = wave  # Left channel
                arr[i][1] = wave  # Right channel
            
            sound = pygame.sndarray.make_sound(arr)
            sound.play()
            
            # Wait for sound to finish
            while pygame.mixer.get_busy():
                time.sleep(0.1)
        
        except Exception as e:
            logger.error(f"❌ Error generating beep: {e}")
    
    def _play_aplay(self, sound_file: str, alarm_id: str = None) -> bool:
        """Play alarm sound using aplay (ALSA)."""
        try:
            logger.info(f"🔊 DEBUG: Attempting aplay with file: {sound_file}")
            
            if os.path.exists(sound_file):
                # Force output to the detected analog audio device to avoid HDMI routing issues
                # Use a loop to repeat the audio file continuously
                logger.info(f"🔊 DEBUG: Starting aplay subprocess for {sound_file} on {self.alsa_output} (looped)")

                # Create clean environment without PulseAudio variables that conflict with ALSA
                clean_env = os.environ.copy()
                clean_env.pop('PULSE_RUNTIME_PATH', None)
                clean_env.pop('PULSE_RUNTIME_DIR', None)
                clean_env['ALSA_PCM_CARD'] = self.alsa_card  # Force ALSA to use detected card
                clean_env['ALSA_PCM_DEVICE'] = self.alsa_device  # Force ALSA to use detected device

                process = subprocess.Popen(
                    ['sh', '-c', f'while true; do aplay -D {self.alsa_output} "{sound_file}"; done'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid,
                    env=clean_env,
                )  # Create new process group with clean env

                # Store process reference if alarm_id provided (for termination)
                if alarm_id:
                    self.active_processes[alarm_id] = process
                    logger.info(f"🔊 DEBUG: Stored process {process.pid} for alarm {alarm_id}")

                # Give process a moment to fail fast if there's an audio error
                time.sleep(0.1)

                # Check if process is still running (don't wait for completion)
                if process.poll() is None:
                    # Process is still running, which is good for continuous playback
                    logger.info(f"🔊 DEBUG: aplay process {process.pid} is running")
                    return True
                else:
                    # Process finished (either success or error)
                    stdout, stderr = process.communicate()
                    logger.info(f"🔊 DEBUG: aplay finished with return code: {process.returncode}")
                
                if process.returncode != 0:
                    logger.error(f"❌ aplay failed with return code {process.returncode}")
                    logger.error(f"❌ aplay stdout: {stdout.decode() if stdout else 'None'}")
                    logger.error(f"❌ aplay stderr: {stderr.decode() if stderr else 'None'}")
                
                # Clean up process reference
                if alarm_id and alarm_id in self.active_processes:
                    self.active_processes.pop(alarm_id, None)
                
                return process.returncode == 0
            else:
                # Use speaker-test as fallback - this is more reliable than missing WAV files
                logger.info(f"🔊 Sound file {sound_file} not found, using speaker-test fallback")
                return self._play_speaker_test(sound_file, alarm_id)
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ aplay CalledProcessError: {e}")
            return self._play_speaker_test(sound_file, alarm_id)
        except Exception as e:
            logger.error(f"❌ aplay unexpected error: {e}")
            return False
    
    def _play_paplay(self, sound_file: str) -> bool:
        """Play alarm sound using paplay (PulseAudio)."""
        try:
            if os.path.exists(sound_file):
                subprocess.run(['paplay', sound_file], check=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                # Use speaker-test as fallback
                return self._play_speaker_test(sound_file, None)
            return True
        except subprocess.CalledProcessError:
            return False
    
    def _play_afplay(self, sound_file: str) -> bool:
        """Play alarm sound using afplay (macOS)."""
        try:
            # If a specific sound file exists use it first
            if os.path.exists(sound_file):
                subprocess.run(['afplay', sound_file], check=True, timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True

            # Fallback to system sounds on macOS
            sound_paths = [
                '/System/Library/Sounds/Alarm.aiff',
                '/System/Library/Sounds/Glass.aiff',
                '/System/Library/Sounds/Ping.aiff',
                '/System/Library/Sounds/Sosumi.aiff'
            ]
            
            for sound_path in sound_paths:
                if os.path.exists(sound_path):
                    subprocess.run(['afplay', sound_path], 
                                 check=True, timeout=5,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return True
            
            # If no system sounds found, generate a beep
            subprocess.run(['say', 'alarm'], check=True, timeout=3,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
            
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
    
    def _play_speaker_test(self, sound_file: str = None, alarm_id: str = None) -> bool:
        """Play alarm sound using speaker-test (fallback)."""
        try:
            # Force output to the detected analog audio device and play a 2-second 800Hz tone
            process = subprocess.Popen(
                ['speaker-test', '-D', self.alsa_output, '-t', 'sine', '-f', '800', '-l', '2'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            
            # Store process reference if alarm_id provided (for termination)
            if alarm_id:
                self.active_processes[alarm_id] = process
            
            # Wait for process to complete (with timeout)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                return False
            
            # Clean up process reference
            if alarm_id and alarm_id in self.active_processes:
                self.active_processes.pop(alarm_id, None)
            
            logger.info("🔊 speaker-test alarm sound played successfully")
            return process.returncode == 0
        except Exception as e:
            logger.error(f"❌ speaker-test failed: {e}")
            return False

    def _play_console_beep(self) -> bool:
        """Fallback beep using the console bell character."""
        try:
            print("\a", end="", flush=True)
            time.sleep(0.2)
            return True
        except Exception as e:
            logger.error(f"❌ Console beep failed: {e}")
            return False
    
    def test_audio(self) -> bool:
        """Test audio output."""
        logger.info(f"🔊 Testing audio output using method: {self.audio_method}")
        env_path = os.environ.get('ALARM_SOUND_FILE')
        if env_path:
            sound_file = env_path
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            sound_file = os.path.join(base_dir, 'sounds', 'alarm.wav')
        return self._play_alarm_sound_once(sound_file)

# Global audio manager instance
_audio_manager: Optional[PiAudioManager] = None

def get_audio_manager() -> PiAudioManager:
    """Get global audio manager instance."""
    global _audio_manager
    if _audio_manager is None:
        _audio_manager = PiAudioManager()
    return _audio_manager

# Convenience functions
def start_alarm_sound(alarm_id: str, alarm_label: str = "Alarm") -> bool:
    """Start playing alarm sound."""
    return get_audio_manager().start_alarm_sound(alarm_id, alarm_label)

def stop_alarm_sound(alarm_id: str) -> bool:
    """Stop playing alarm sound."""
    return get_audio_manager().stop_alarm_sound(alarm_id)

def stop_all_alarms():
    """Stop all alarm sounds."""
    get_audio_manager().stop_all_alarms()

def test_audio() -> bool:
    """Test audio output."""
    return get_audio_manager().test_audio()

if __name__ == "__main__":
    # Test the audio system
    logging.basicConfig(level=logging.INFO)
    
    print("🔊 Testing Pi Audio Manager...")
    
    # Test audio
    if test_audio():
        print("✅ Audio test successful!")
    else:
        print("❌ Audio test failed!")
    
    # Test alarm sound
    print("🚨 Testing alarm sound (3 seconds)...")
    start_alarm_sound("test", "Test Alarm")
    time.sleep(3)
    stop_alarm_sound("test")
    
    print("🎉 Audio manager test complete!")
