import os
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk
import shutil
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from datetime import datetime
import argparse
import logging
import platform

from assemble import assemble

# Set up logging for debugging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

class DropHandler(FileSystemEventHandler):
    def __init__(self, file_list, listbox, working_folder):
        self.file_list = file_list
        self.listbox = listbox
        self.working_folder = working_folder
        self.valid_extensions = ('.jpg', '.jpeg', '.png', '.mp4', '.mov', '.avi')
        self.insertion_counter = 0
        self.processed_files = set()  # Track processed files to avoid re-renaming

    def on_created(self, event):
        if not event.is_directory:
            file_path = event.src_path
            logging.debug(f"Detected file creation: {file_path}")
            if file_path.lower().endswith(self.valid_extensions) and file_path not in self.processed_files:
                new_file_path = self.rename_file(file_path)
                if new_file_path:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.file_list.append((new_file_path, timestamp))
                    self.processed_files.add(new_file_path)  # Mark as processed
                    self.update_listbox()
            else:
                logging.debug(f"Ignored file (invalid extension or already processed): {file_path}")

    def on_moved(self, event):
        if not event.is_directory:
            file_path = event.dest_path
            logging.debug(f"Detected file move: {file_path}")
            if file_path.lower().endswith(self.valid_extensions) and file_path not in self.processed_files:
                new_file_path = self.rename_file(file_path)
                if new_file_path:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.file_list.append((new_file_path, timestamp))
                    self.processed_files.add(new_file_path)  # Mark as processed
                    self.update_listbox()
            else:
                logging.debug(f"Ignored moved file (invalid extension or already processed): {file_path}")

    def rename_file(self, file_path):
        try:
            self.insertion_counter += 1
            file_name = os.path.basename(file_path)
            # Strip any existing #<number> prefixes to avoid stacking
            clean_file_name = file_name
            while clean_file_name.startswith('#') and clean_file_name[1].isdigit():
                clean_file_name = clean_file_name[2:]  # Remove #<number>
            new_file_name = f"#{self.insertion_counter}#{clean_file_name}"
            new_file_path = os.path.join(self.working_folder, new_file_name)
            os.rename(file_path, new_file_path)
            logging.debug(f"Renamed file: {file_path} to {new_file_path}")
            return new_file_path
        except Exception as e:
            logging.error(f"Failed to rename file {file_path}: {e}")
            return None

    def update_listbox(self):
        self.listbox.delete(0, tk.END)
        for file_path, ts in self.file_list:
            self.listbox.insert(tk.END, f"{ts}: {os.path.basename(file_path)}")

class DragDropApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Drag and Drop Carousel Video Creator")
        self.root.geometry("800x600")

        # Create temporary working folder
        self.working_folder = tempfile.mkdtemp(prefix="media_drop_")
        self.file_list = []
        logging.info(f"Temporary folder created: {self.working_folder}")

        # GUI Elements
        self.label = tk.Label(root, text=f"Working Folder: {self.working_folder}")
        self.label.pack(pady=5)

        self.open_folder_button = tk.Button(root, text="Open Working Folder", command=self.open_working_folder)
        self.open_folder_button.pack(pady=5)

        self.listbox = tk.Listbox(root, width=80, height=10)
        self.listbox.pack(pady=5)

        # Form for parameters
        self.form_frame = ttk.LabelFrame(root, text="Video Parameters", padding=10)
        self.form_frame.pack(pady=10, padx=10, fill="x")

        # Default values
        self.defaults = {
            'input_dir': "queen 14",
            'output_file': "iku_2.mp4",
            'resolution': "1080p",
            'image_duration': 11.0,
            'max_video_duration': 11.0,
            'blur_radius': 15.0,
            'zoom_start': 1.6,
            'zoom_end': 1.8,
            'zoom_direction': 'top',  # New default value
            'overlay_scale': 0.8,
            'transition_duration': 2.0,
            'transition_type': 'fadeblack',
            'text_fade_in': 4.0,
            'text_fade_out': 2.0,
            'background_opacity': 0.8,
            'threads': 2,
            'draw_text': False
        }

        # Form fields
        ttk.Label(self.form_frame, text="Output File:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        self.output_file_var = tk.StringVar(value=self.defaults['output_file'])
        ttk.Entry(self.form_frame, textvariable=self.output_file_var).grid(row=0, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Resolution:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        self.resolution_var = tk.StringVar(value=self.defaults['resolution'])
        ttk.Combobox(self.form_frame, textvariable=self.resolution_var, values=["720p", "1080p", "4k"]).grid(row=1, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Image Duration (s):").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        self.image_duration_var = tk.DoubleVar(value=self.defaults['image_duration'])
        ttk.Entry(self.form_frame, textvariable=self.image_duration_var).grid(row=2, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Max Video Duration (s):").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        self.max_video_duration_var = tk.DoubleVar(value=self.defaults['max_video_duration'])
        ttk.Entry(self.form_frame, textvariable=self.max_video_duration_var).grid(row=3, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Blur Radius:").grid(row=4, column=0, sticky="w", padx=5, pady=2)
        self.blur_radius_var = tk.DoubleVar(value=self.defaults['blur_radius'])
        ttk.Entry(self.form_frame, textvariable=self.blur_radius_var).grid(row=4, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Zoom Start:").grid(row=5, column=0, sticky="w", padx=5, pady=2)
        self.zoom_start_var = tk.DoubleVar(value=self.defaults['zoom_start'])
        ttk.Entry(self.form_frame, textvariable=self.zoom_start_var).grid(row=5, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Zoom End:").grid(row=6, column=0, sticky="w", padx=5, pady=2)
        self.zoom_end_var = tk.DoubleVar(value=self.defaults['zoom_end'])
        ttk.Entry(self.form_frame, textvariable=self.zoom_end_var).grid(row=6, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Zoom Direction:").grid(row=7, column=0, sticky="w", padx=5, pady=2)
        self.zoom_direction_var = tk.StringVar(value=self.defaults['zoom_direction'])
        ttk.Combobox(self.form_frame, textvariable=self.zoom_direction_var, values=[
            'center', 'top', 'bottom', 'right', 'left', 'top-right', 'top-left', 'bottom-right', 'bottom-left'
        ]).grid(row=7, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Overlay Scale (0-1):").grid(row=8, column=0, sticky="w", padx=5, pady=2)
        self.overlay_scale_var = tk.DoubleVar(value=self.defaults['overlay_scale'])
        ttk.Entry(self.form_frame, textvariable=self.overlay_scale_var).grid(row=8, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Transition Duration (s):").grid(row=9, column=0, sticky="w", padx=5, pady=2)
        self.transition_duration_var = tk.DoubleVar(value=self.defaults['transition_duration'])
        ttk.Entry(self.form_frame, textvariable=self.transition_duration_var).grid(row=9, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Transition Type:").grid(row=10, column=0, sticky="w", padx=5, pady=2)
        self.transition_type_var = tk.StringVar(value=self.defaults['transition_type'])
        ttk.Combobox(self.form_frame, textvariable=self.transition_type_var, values=[
            'fade', 'fadeblack', 'fadewhite', 'wipeleft', 'wiperight', 'wipeup', 'wipedown',
            'slideleft', 'slideright', 'slideup', 'slidedown', 'circlecrop', 'circleopen',
            'circleclose', 'vertopen', 'vertclose', 'horzopen', 'horzclose', 'dissolve',
            'pixelize', 'radial', 'hlslice', 'vuslice', 'hblur', 'fadegrays', 'wipetl',
            'wipetr', 'wipebl', 'wipetr', 'squeezev', 'squeezeh', 'zoomin'
        ]).grid(row=10, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Text Fade-In (s):").grid(row=11, column=0, sticky="w", padx=5, pady=2)
        self.text_fade_in_var = tk.DoubleVar(value=self.defaults['text_fade_in'])
        ttk.Entry(self.form_frame, textvariable=self.text_fade_in_var).grid(row=11, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Text Fade-Out (s):").grid(row=12, column=0, sticky="w", padx=5, pady=2)
        self.text_fade_out_var = tk.DoubleVar(value=self.defaults['text_fade_out'])
        ttk.Entry(self.form_frame, textvariable=self.text_fade_out_var).grid(row=12, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Background Opacity (0-1):").grid(row=13, column=0, sticky="w", padx=5, pady=2)
        self.background_opacity_var = tk.DoubleVar(value=self.defaults['background_opacity'])
        ttk.Entry(self.form_frame, textvariable=self.background_opacity_var).grid(row=13, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Threads:").grid(row=14, column=0, sticky="w", padx=5, pady=2)
        self.threads_var = tk.IntVar(value=self.defaults['threads'])
        ttk.Entry(self.form_frame, textvariable=self.threads_var).grid(row=14, column=1, sticky="ew", padx=5, pady=2)

        ttk.Label(self.form_frame, text="Draw Text:").grid(row=15, column=0, sticky="w", padx=5, pady=2)
        self.draw_text_var = tk.BooleanVar(value=self.defaults['draw_text'])
        ttk.Checkbutton(self.form_frame, variable=self.draw_text_var).grid(row=15, column=1, sticky="w", padx=5, pady=2)

        self.form_frame.columnconfigure(1, weight=1)

        self.process_button = tk.Button(root, text="Process Folder", command=self.process_folder)
        self.process_button.pack(pady=10)

        # Set up file system watcher
        self.event_handler = DropHandler(self.file_list, self.listbox, self.working_folder)
        self.observer = Observer()
        try:
            self.observer.schedule(self.event_handler, self.working_folder, recursive=False)
            self.observer.start()
            logging.info("File system observer started")
        except Exception as e:
            logging.error(f"Failed to start observer: {e}")
            messagebox.showerror("Error", f"Failed to monitor folder: {e}")

        # Clean up on window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def open_working_folder(self):
        """Open the temporary working folder in the system's file explorer."""
        try:
            if platform.system() == "Windows":
                os.startfile(self.working_folder)
            elif platform.system() == "Darwin":  # macOS
                subprocess.run(["open", self.working_folder], check=True)
            else:  # Linux and others
                subprocess.run(["xdg-open", self.working_folder], check=True)
            logging.info(f"Opened working folder: {self.working_folder}")
        except Exception as e:
            logging.error(f"Failed to open folder: {e}")
            messagebox.showerror("Error", f"Failed to open folder: {e}")

    def process_folder(self):
        try:
            if not self.file_list:
                raise ValueError("No files have been dropped into the working folder")
            # Create argparse Namespace with UI values
            args = argparse.Namespace(
                input_dir=self.working_folder,
                output_file=self.output_file_var.get(),
                resolution=self.resolution_var.get(),
                width=None,
                height=None,
                image_duration=self.image_duration_var.get(),
                max_video_duration=self.max_video_duration_var.get(),
                blur_radius=self.blur_radius_var.get(),
                zoom_start=self.zoom_start_var.get(),
                zoom_end=self.zoom_end_var.get(),
                zoom_direction=self.zoom_direction_var.get(),  # New parameter
                overlay_scale=self.overlay_scale_var.get(),
                transition_duration=self.transition_duration_var.get(),
                transition_type=self.transition_type_var.get(),
                text_fade_in=self.text_fade_in_var.get(),
                text_fade_out=self.text_fade_out_var.get(),
                background_opacity=self.background_opacity_var.get(),
                threads=self.threads_var.get(),
                draw_text=self.draw_text_var.get()
            )
            result = assemble(args)
            messagebox.showinfo("Success", result)
        except Exception as e:
            logging.error(f"Processing failed: {e}")
            messagebox.showerror("Error", f"Processing failed: {str(e)}")

    def on_closing(self):
        # Stop observer and clean up temporary folder
        try:
            self.observer.stop()
            self.observer.join(timeout=2.0)
        except Exception as e:
            logging.error(f"Error stopping observer: {e}")
        try:
            shutil.rmtree(self.working_folder)
            logging.info(f"Temporary folder deleted: {self.working_folder}")
        except Exception as e:
            logging.error(f"Error cleaning up folder: {e}")
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = DragDropApp(root)
    root.mainloop()