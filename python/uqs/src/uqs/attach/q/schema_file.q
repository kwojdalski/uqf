/ schema_file.q - read THIS tickerplant's schema file, or add or take out ONE
/ uqs-attach block in it (#1651). For a stack on another machine, the process
/ that reads the file at every start is the one that can reach it.
/ .
/ The file is the one the process was started with (-schemafile), never a path
/ from the request. Python (persist.py) composes every change, and this checks
/ it before it touches anything:
/   * the file's md5 is still the one the change was computed from;
/   * an append is exactly one whole block, `/ BEGIN uqs-attach <block> <token>`
/     to `/ END uqs-attach <block>`, after a newline only when the file lacks a
/     final one, for a block the file does not hold yet;
/   * a cut removes exactly such a block, from the start of a line, plus at
/     most the file's final newline.
/ An append opens the file for appending, so the bytes already there are never
/ rewritten. A cut writes the old bytes to <file>.uqs-attach.bak and the new
/ ones to <file>.uqs-attach.tmp, then `cp`s the new ones over the file - which
/ keeps its mode, owner and inode, where q's own `1:` makes a new file with the
/ default mode - checks what it reads back and deletes both. If that copy
/ fails, the backup is left for a person. (Not `cat tmp > file`: q's `system`
/ adds its own stdout redirect, which wins, and the file is left empty.)
/ .
/ @param req JSON {"action": "read"}
/   | {"action": "append", "expect": md5, "block", "token", "bytes": hex}
/   | {"action": "cut", "expect": md5, "block", "token", "start", "end", "trim"}
/   - start and end are byte offsets, trim drops the final newline after the cut
/ @return JSON {"status": "read"|"appended"|"cut"|"absent"|"refused"|"failed",
/   "path", "detail", "md5"} - read adds "bytes" (hex) and "writable"
{[req]
 r:.j.k $[10h=type req;req;string req];
 prm:@[value;`.proc.params;()!()];
 p:$[`schemafile in key prm;"," sv prm`schemafile;""];
 out:{[p;s;d;x] .j.j (`status`path`detail!(s;p;d)),x};
 if[not count p;:out[p;"refused";"the process was not started with -schemafile";()!()]];
 f:hsym`$p;
 if[()~key f;:out[p;"absent";"the file does not exist";()!()]];
 cur:"c"$read1 f;
 sum5:{raze string md5 x};
 act:r`action;
 if[act~"read";
  :out[p;"read";"";`md5`bytes`writable!(sum5 cur;$[count cur;raze string "x"$cur;""];@[{hclose hopen x;1b};f;0b])]];
 if[not act in ("append";"cut");:out[p;"refused";"unknown action";()!()]];
 if[not (r`expect)~sum5 cur;:out[p;"refused";"the file changed since it was read";()!()]];
 blk:r`block;tok:r`token;
 ok:{(count x) and all x in .Q.an,"-+."};
 if[not (ok blk) and ok tok;:out[p;"refused";"the block name or token is not one uqs writes";()!()]];
 begin:"/ BEGIN uqs-attach ",blk," ",tok,"\n";
 fin:"/ END uqs-attach ",blk,"\n";
 whole:{[b;e;x] (((count b)+count e)<=count x) and (b~(count b)#x) and (e~neg[count e]#x) and (1=count x ss "/ BEGIN uqs-attach ") and 1=count x ss "/ END uqs-attach "};
 if[act~"append";
  add:"c"$"X"$2 cut r`bytes;
  lead:(count cur) and not "\n"~last cur;
  if[lead and not "\n"~first add;:out[p;"refused";"the file lacks a final newline and the block does not add one";()!()]];
  if[not whole[begin;fin;(`long$lead) _ add];:out[p;"refused";"not exactly one whole uqs-attach block";()!()]];
  if[count cur ss "/ BEGIN uqs-attach ",blk," ";:out[p;"refused";"the file already holds a block of that name";()!()]];
  w:.[{h:hopen x;h y;hclose h;""};(f;add);{x}];
  if[count w;:out[p;"failed";w;()!()]];
  :out[p;"appended";"";enlist[`md5]!enlist sum5 "c"$read1 f]];
 s:`long$r`start;e:`long$r`end;
 if[not (0<=s) and (s<e) and e<=count cur;:out[p;"refused";"the span is outside the file";()!()]];
 if[(s>0) and not "\n"~cur s-1;:out[p;"refused";"the span does not start a line";()!()]];
 if[not whole[begin;fin;s _ e#cur];:out[p;"refused";"the span is not exactly this block";()!()]];
 rest:(s#cur),e _ cur;
 if[r`trim;
  if[not "\n"~last rest;:out[p;"refused";"no final newline to take out";()!()]];
  rest:-1_rest];
 if[p like "*'*";:out[p;"refused";"the path holds a quote";()!()]];
 bak:hsym`$p,".uqs-attach.bak";tmp:hsym`$p,".uqs-attach.tmp";
 w:@[{[b;t;c;n;p] b 1: "x"$c;t 1: "x"$n;system "cp '",(1_string t),"' '",p,"'";""}[bak;tmp;cur;rest];p;{x}];
 if[count w;:out[p;"failed";"the copy failed (",w,"); the old bytes are in ",1_string bak;()!()]];
 if[not rest~"c"$read1 f;:out[p;"failed";"the file does not read back as written; the old bytes are in ",1_string bak;()!()]];
 hdel each (tmp;bak);
 out[p;"cut";"";enlist[`md5]!enlist sum5 rest]}
