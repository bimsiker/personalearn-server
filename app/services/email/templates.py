from app.services.email import EmailMessage


def verification_email(to: str, name: str, code: str, ttl_minutes: int) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Your PersonaLearn verification code",
        text=(
            f"Hi {name},\n\n"
            f"Your PersonaLearn verification code is: {code}\n\n"
            f"It expires in {ttl_minutes} minutes. "
            "If you didn't sign up, you can ignore this email."
        ),
    )


def password_reset_email(to: str, name: str, link: str, ttl_minutes: int) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Reset your PersonaLearn password",
        text=(
            f"Hi {name},\n\n"
            f"Use this link to choose a new password:\n{link}\n\n"
            f"The link expires in {ttl_minutes} minutes and can only be used once. "
            "If you didn't ask for this, you can ignore this email."
        ),
    )
