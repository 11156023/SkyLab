export default function MIcon({ name, size = 20, className, filled = false, spin = false, ...rest }) {
  const classes = [
    filled ? "material-icons" : "material-icons-outlined",
    spin ? "micon-spin" : "",
    className ?? "",
  ].filter(Boolean).join(" ");
  return (
    <span
      className={classes}
      style={{ fontSize: size, lineHeight: 1 }}
      aria-hidden="true"
      {...rest}
    >
      {name}
    </span>
  );
}
