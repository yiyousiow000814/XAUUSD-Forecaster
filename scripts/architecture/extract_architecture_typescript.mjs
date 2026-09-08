// Source AST/span approach reused from #321, without its recursive file walk,
// regex dispatch guesses, or runtime certainty. Input files are selected by the
// shared compiler. The TypeScript package is verified before this entry runs.
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const request = JSON.parse(readFileSync(0, 'utf8'));
const metadata = JSON.parse(readFileSync(join(request.package, 'package.json'), 'utf8'));
if (metadata.name !== 'typescript' || metadata.version !== request.version) {
  throw new Error('ARCHITECTURE_TOOL_INTEGRITY_FAILED:typescript-version');
}
const ts = createRequire(import.meta.url)(join(request.package, 'lib/typescript.js'));
if (ts.version !== request.version) throw new Error('ARCHITECTURE_TOOL_INTEGRITY_FAILED:typescript-api');
const symbols = [], edges = [];

for (const file of request.files) {
  const source = ts.createSourceFile(file.path, file.content, ts.ScriptTarget.Latest, true,
    file.path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  if (source.parseDiagnostics.length) {
    const first = source.parseDiagnostics[0];
    const position = source.getLineAndCharacterOfPosition(first.start ?? 0);
    throw new Error(`ARCHITECTURE_PARSE_FAILED:${file.path}:${position.line + 1}:${first.code}`);
  }
  const scope = [];
  const span = node => {
    const start = source.getLineAndCharacterOfPosition(node.getStart(source));
    const end = source.getLineAndCharacterOfPosition(node.getEnd());
    return { line: start.line + 1, column: start.character + 1,
      end_line: end.line + 1, end_column: end.character + 1 };
  };
  const staticName = node => node && (ts.isIdentifier(node) || ts.isPrivateIdentifier(node)
    || ts.isStringLiteralLike(node) || ts.isNumericLiteral(node)) ? node.text : null;
  const target = node => {
    if (ts.isIdentifier(node)) return node.text;
    if (node.kind === ts.SyntaxKind.ThisKeyword) return 'this';
    if (node.kind === ts.SyntaxKind.SuperKeyword) return 'super';
    if (ts.isPropertyAccessExpression(node)) return `${target(node.expression)}.${node.name.text}`;
    if (ts.isElementAccessExpression(node)) return `${target(node.expression)}[${staticName(node.argumentExpression) ?? '<dynamic>'}]`;
    if (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isNonNullExpression(node)) return target(node.expression);
    if (node.kind === ts.SyntaxKind.ImportKeyword) return 'import';
    return '<dynamic-call>';
  };
  const edge = (node, kind, destination, detail = {}) => edges.push({
    source: `${file.path}::${scope.join('.') || '<module>'}`, target: destination,
    kind, ...span(node), resolution: 'UNKNOWN', extractor: 'typescript-ast',
    binding: 'Source syntax only; runtime dispatch is not executed or proven', ...detail,
  });
  function declaration(node) {
    let name = staticName(node.name);
    if (!name && (ts.isArrowFunction(node) || ts.isFunctionExpression(node))) {
      const parent = node.parent;
      if (ts.isVariableDeclaration(parent) || ts.isPropertyAssignment(parent) || ts.isPropertyDeclaration(parent)) {
        name = staticName(parent.name);
      } else if (ts.isCallExpression(parent) && ts.isVariableDeclaration(parent.parent)) {
        // useCallback/useMemo-style initializer context is a stable source
        // label, not proof that the wrapper returns or runs this callback.
        name = staticName(parent.parent.name);
        const argument = parent.arguments.indexOf(node);
        if (name && argument > 0) name += `<argument${argument}>`;
      }
    }
    if (ts.isConstructorDeclaration(node)) name = 'constructor';
    if (!name) { const position = span(node); name = `<anonymous@${position.line}:${position.column}>`; }
    if (ts.isGetAccessorDeclaration(node)) name = `get ${name}`;
    if (ts.isSetAccessorDeclaration(node)) name = `set ${name}`;
    // Type overloads are declarations, not duplicate executable bodies.
    if (ts.isFunctionLike(node) && !node.body) name += `<signature@${span(node).line}>`;
    scope.push(name);
    symbols.push({ id: `${file.path}::${scope.join('.')}`, name: scope.join('.'), path: file.path,
      ...span(node), language: file.path.endsWith('.tsx') ? 'tsx' : 'typescript',
      syntactic_owner: file.path, syntax_kind: ts.SyntaxKind[node.kind] });
    ts.forEachChild(node, visit);
    scope.pop();
  }
  function objectDeclaration(node, name, object = node.initializer) {
    scope.push(name);
    symbols.push({ id: `${file.path}::${scope.join('.')}`, name: scope.join('.'), path: file.path,
      ...span(node), language: file.path.endsWith('.tsx') ? 'tsx' : 'typescript',
      syntactic_owner: file.path, syntax_kind: `Object${ts.SyntaxKind[node.kind]}` });
    for (const member of object.properties) {
      edge(member, 'declares_member', staticName(member.name) ?? '<dynamic-member>',
        { binding: 'Object member declaration only; lookup behavior and runtime value UNKNOWN' });
    }
    ts.forEachChild(node, visit); scope.pop();
  }
  function visit(node) {
    if (ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) || ts.isArrowFunction(node)
      || ts.isClassDeclaration(node) || ts.isClassExpression(node) || ts.isMethodDeclaration(node)
      || ts.isConstructorDeclaration(node) || ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node)
      || ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node)) {
      declaration(node); return;
    }
    if ((ts.isVariableDeclaration(node) || ts.isPropertyAssignment(node) || ts.isPropertyDeclaration(node))
      && ts.isObjectLiteralExpression(node.initializer ?? node)) {
      // Preserve named object ownership of methods, without inventing a class.
      const position = span(node);
      const name = staticName(node.name) ?? `<computed@${position.line}:${position.column}>`;
      objectDeclaration(node, name); return;
    }
    if (ts.isObjectLiteralExpression(node)) {
      const parent = node.parent;
      const named = (ts.isVariableDeclaration(parent) || ts.isPropertyAssignment(parent) || ts.isPropertyDeclaration(parent))
        && parent.initializer === node;
      if (!named) {
        const position = span(node);
        objectDeclaration(node, `<object@${position.line}:${position.column}>`, node); return;
      }
    }
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
      const literal = ts.isStringLiteralLike(node.moduleSpecifier);
      edge(node, 'requires', literal ? node.moduleSpecifier.text : '<dynamic-module>',
        { syntax_kind: ts.SyntaxKind[node.kind], type_only: Boolean(node.isTypeOnly || node.importClause?.isTypeOnly) });
    }
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
      const destination = target(node.expression);
      const argument = node.arguments?.[0];
      const argumentSyntax = argument && ts.isStringLiteralLike(argument)
        ? { first_argument_literal: argument.text }
        : argument && ts.isTemplateExpression(argument)
          ? { first_argument_template: [argument.head.text, ...argument.templateSpans.map(part => part.literal.text)] }
          : {};
      edge(node, ts.isNewExpression(node) ? 'constructs' : 'calls', destination, argumentSyntax);
      if (node.expression.kind === ts.SyntaxKind.ImportKeyword) {
        const argument = node.arguments?.[0];
        edge(node, 'requires', argument && ts.isStringLiteralLike(argument) ? argument.text : '<dynamic-module>');
      }
      if (ts.isPropertyAccessExpression(node.expression) && ['prepare', 'exec'].includes(node.expression.name.text)) {
        const argument = node.arguments?.[0];
        const literal = argument && ts.isStringLiteralLike(argument);
        edge(node, 'sql', destination, { statement: literal ? argument.text : '<dynamic SQL>',
          resolution: literal ? 'LITERAL' : 'UNKNOWN',
          binding: 'Literal argument to prepare/exec syntax; D1 receiver and execution UNKNOWN' });
      }
    }
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      edge(node, 'renders', target(node.tagName), { binding: 'JSX tag syntax; component binding and render execution UNKNOWN' });
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
}
process.stdout.write(JSON.stringify({ symbols, edges, tool: { name: 'typescript', version: ts.version } }));
