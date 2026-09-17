//! In-process timing without filesystem reads or process startup in the timer.
use std::{fs, time::Instant};
fn main() {
    let sources: Vec<_> = std::env::args()
        .skip(1)
        .map(|p| {
            let s = fs::read_to_string(&p).unwrap();
            (p, s)
        })
        .collect();
    for (p, s) in &sources {
        std::hint::black_box(q_lint_rs::lint(s, p, false));
    }
    let mut samples = vec![];
    for _ in 0..10 {
        let start = Instant::now();
        for (p, s) in &sources {
            std::hint::black_box(q_lint_rs::lint(s, p, false));
        }
        samples.push(start.elapsed().as_secs_f64());
    }
    println!(
        "{}",
        serde_json::json!({"seconds":samples,"files":sources.len()})
    );
}
