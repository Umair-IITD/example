import os

# Directories and files you want to exclude from the structure to keep it clean
EXCLUDE_DIRS = {'.venv','venv','.vscode','__pycache__','.git','.pytest_cache','.idea'}
EXCLUDE_FILES = {'.DS_Store'}

def generate_tree(dir_path, prefix=""):
    """Recursively builds the visual tree structure as a list of strings."""
    try:
        items = sorted(os.listdir(dir_path))
    except PermissionError:
        return []

    # Filter out excluded items
    visible_items = []
    for item in items:
        path = os.path.join(dir_path, item)
        if os.path.isdir(path) and item in EXCLUDE_DIRS:
            continue
        if os.path.isfile(path) and item in EXCLUDE_FILES:
            continue
        visible_items.append(item)

    tree_lines = []
    total_items = len(visible_items)
    
    for index, item in enumerate(visible_items):
        path = os.path.join(dir_path, item)
        is_last = (index == total_items - 1)
        
        # Select appropriate branching characters
        connector = "└── " if is_last else "├── "
        tree_lines.append(f"{prefix}{connector}{item}")
        
        if os.path.isdir(path):
            # Extend the prefix lines downward for next sub-levels
            extension = "    " if is_last else "│   "
            tree_lines.extend(generate_tree(path, prefix + extension))
            
    return tree_lines

def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    project_name = os.path.basename(root_dir)
    
    # Header for your text file
    structure = [f"{project_name}/"]
    structure.extend(generate_tree(root_dir))
    
    output_text = "\n".join(structure)
    
    # Print out to the terminal console
    print("\n--- Project Structure Generated ---")
    print(output_text)
    print("-----------------------------------\n")
    
    # Save directly to structure.txt in your root folder
    output_file = os.path.join(root_dir, "structure.txt")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(output_text)
        
    print(f"Success! Complete structure saved to: {output_file}")

if __name__ == "__main__":
    main()