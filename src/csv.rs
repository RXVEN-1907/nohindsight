//! nohindsight csv - the hand-rolled reader for the CLI's two inputs.
//!
//! Zero dependencies means no csv crate, so this module is the reader.  It
//! is deliberately small and its limits are stated:
//!
//! * RFC 4180 quoting: a field may be wrapped in double quotes, may contain
//!   commas, and `""` inside quotes is a literal quote;
//! * quoted fields may NOT span lines - the reader is line-based, which is
//!   the whole truth for the one-record-per-line returns and bar files the
//!   CLI consumes;
//! * a trailing newline is not a row; a row of only whitespace is skipped
//!   for the returns reader but preserved by [`parse_line`] for callers
//!   that care about column counts.

/// Parse one CSV line into fields (RFC 4180 quoting, line-based).
pub fn parse_line(line: &str) -> Vec<String> {
    let mut fields = Vec::new();
    let mut cur = String::new();
    let mut in_quotes = false;
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        if in_quotes {
            if c == '"' {
                match chars.peek() {
                    Some('"') => {
                        cur.push('"');
                        chars.next();
                    }
                    _ => in_quotes = false,
                }
            } else {
                cur.push(c);
            }
        } else {
            match c {
                '"' => in_quotes = true,
                ',' => {
                    fields.push(std::mem::take(&mut cur));
                }
                _ => cur.push(c),
            }
        }
    }
    fields.push(cur);
    fields
}

/// Parse a whole document: lines, each into fields.  `\r\n` endings are
/// trimmed (finance CSVs come from Excel more often than not).
pub fn parse(text: &str) -> Vec<Vec<String>> {
    text.lines()
        .map(|line| parse_line(line.strip_suffix('\r').unwrap_or(line)))
        .collect()
}

/// A number from a CSV cell: trimmed, empty refused.  The error names the
/// cell text - "is not a number" like the Python CLI's.
pub fn cell_f64(cell: &str) -> Result<f64, String> {
    let trimmed = cell.trim();
    trimmed
        .parse::<f64>()
        .map_err(|_| format!("{trimmed:?} is not a number"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quoted_fields_and_escaped_quotes() {
        assert_eq!(parse_line("a,b,c"), vec!["a", "b", "c"]);
        assert_eq!(parse_line("a,\"b,c\",d"), vec!["a", "b,c", "d"]);
        assert_eq!(parse_line("\"say \"\"hi\"\"\",2"), vec!["say \"hi\"", "2"]);
        assert_eq!(parse_line("one"), vec!["one"]);
        assert_eq!(parse_line("a,,c"), vec!["a", "", "c"]);
        assert_eq!(parse_line("\"a,b\",,\"c\""), vec!["a,b", "", "c"]);
    }

    #[test]
    fn documents_and_cells() {
        let rows = parse("r,ret\n0.01,-0.02\r\n");
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[1], vec!["0.01", "-0.02"]);
        assert_eq!(cell_f64(" 0.5 "), Ok(0.5));
        assert!(cell_f64("nan-ish").is_err());
        assert_eq!(cell_f64("-3e-2"), Ok(-0.03));
    }
}
