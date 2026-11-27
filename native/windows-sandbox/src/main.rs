use std::process;

#[cfg(windows)]
mod launcher;

fn main() {
    #[cfg(windows)]
    process::exit(launcher::main());
    #[cfg(not(windows))]
    process::exit(1);
}
