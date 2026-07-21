import type { ButtonHTMLAttributes, ReactNode } from 'react'

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  children: ReactNode
  size?: 'small' | 'medium'
}

export function IconButton({
  label,
  children,
  className = '',
  size = 'medium',
  type = 'button',
  ...props
}: IconButtonProps) {
  return (
    <button
      {...props}
      aria-label={label}
      className={`icon-button icon-button--${size} ${className}`.trim()}
      title={label}
      type={type}
    >
      {children}
    </button>
  )
}
