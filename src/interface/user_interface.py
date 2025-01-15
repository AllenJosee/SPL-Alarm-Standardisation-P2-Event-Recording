import tkinter as tk

# Create the main window
root = tk.Tk()
root.title("My GUI")

window_width = 700
window_height = 500

# get the screen dimension
screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()

# find the center point
center_x = int(screen_width / 2 - window_width / 2)
center_y = int(screen_height / 2 - window_height / 2)

# set the position of the window to the center of the screen
root.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")

root.resizable(False, False)

# Create a label
label = tk.Label(root, text="Hello, World!")
label.pack()

# Create a button
button = tk.Button(root, text="Click me!", command=lambda: print("Button clicked!"))
button.pack()

# Run the application
root.mainloop()
