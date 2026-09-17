use q_lint_rs::lint;
#[test]
fn mutation_classes_and_valid_boundaries() {
    for (source, code) in [
        ("f:{[] `a`b!1 2 3}", "QT001"),
        ("f:{[r] r+1};f[`bad]", "QT002"),
        ("f:{[a] g:{[b] a+b};g[1]}", "QF005"),
        ("{[a;b]a+b}[1;2;3]", "QA002"),
    ] {
        assert!(lint(source, "t.q", false).iter().any(|f| f.code == code));
    }
    for source in [
        "f:{[k;v] k!v}",
        "f:{[r] r+1};f[2]",
        "f:{[a] g:{[a;b] a+b};g[a;1]}",
        "{[a;b]a+b}[1;]",
    ] {
        assert!(lint(source, "t.q", false).is_empty());
    }
}
#[test]
fn utf16_ranges_and_literal_masking() {
    let f = lint("s:\"😀\";f:{]", "t.q", false);
    assert_eq!(f[0].column, Some(11));
    assert_eq!(f[0].code, "QE001");
    assert!(lint("s:\"{[desc]} / hi\"; / (]", "t.q", false).is_empty());
}
#[test]
fn arbitrary_text_does_not_panic() {
    let chars = [
        'a', '1', 'é', '😀', ' ', '\n', '\r', '\t', '/', '\\', '\"', '\'', ':', ';', '(', ')', '[',
        ']', '{', '}', '`',
    ];
    let mut state = 13u64;
    for _ in 0..2000 {
        let mut s = String::new();
        for _ in 0..80 {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
            s.push(chars[(state >> 32) as usize % chars.len()]);
        }
        assert!(
            std::panic::catch_unwind(|| lint(&s, "t.q", true)).is_ok(),
            "Input: {s:?}"
        );
    }
}

#[test]
fn language_prefixes_preserve_following_q_diagnostics() {
    for prefix in ["p)", "k)"] {
        let foreign = format!("{prefix}def f():\n\n    \"\"\"😀 {{[desc] . ()\\q\n    /\n\n");
        assert!(lint(&foreign, "t.q", true).is_empty());
        let findings = lint(&format!("{foreign}q)f:{{]\n"), "t.q", false);
        assert_eq!(
            (&*findings[0].code, findings[0].line, findings[0].column),
            ("QE001", 6, Some(6))
        );
        let findings = lint(&format!("{foreign}f:{{[desc] desc}}\n"), "t.q", false);
        assert_eq!((&*findings[0].code, findings[0].line), ("QF001", 6));
    }
    assert!(lint("q)/ bad )\nx:1\n", "t.q", false).is_empty());
    assert_eq!(lint("a)x:1\n", "t.q", false)[0].code, "QE001");
}
