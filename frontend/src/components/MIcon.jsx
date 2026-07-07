export default function MIcon({ name, size = 20, className }) {
  return (
    <span
      className={`material-icons-outlined${className ? ` ${className}` : ""}`}
      style={{ fontSize: size, lineHeight: 1 }}
    >
      {name}
    </span>
  );
}
