from CTFd.models import db
import datetime


class Instances(db.Model):  # type: ignore
    __tablename__ = "prism_instances"
    id = db.Column(db.Integer, primary_key=True)
    challenge_id = db.Column(
        db.Integer, db.ForeignKey("dynamic_isolated_challenge.id", ondelete="CASCADE")
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"))
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id", ondelete="CASCADE"))
    started_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    flag = db.Column(db.Text)

    # Relationships
    user = db.relationship("Users", foreign_keys="Instances.user_id", lazy="select")
    team = db.relationship("Teams", foreign_keys="Instances.team_id", lazy="select")
    challenge = db.relationship(
        "DynamicIsolatedChallenge", foreign_keys="Instances.challenge_id", lazy="select"
    )
