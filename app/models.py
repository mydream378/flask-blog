# -*- coding:utf-8 -*-
from . import db, login_manager
from flask_login import UserMixin, AnonymousUserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import TimedJSONWebSignatureSerializer as Serializer
from flask import current_app, request
from datetime import datetime
import hashlib
from markdown import markdown
import bleach

class Permission:
    FOLLOW = 0x01             # 关注用户
    COMMENT = 0x02            # 在他人的文章中发表评论
    WRITE_ARTICLES = 0x04     # 写文章
    MODERATE_COMMENTS = 0x08  # 管理他人发表的评论
    ADMINISTRATOR = 0xff      # 管理者权限

class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=True, unique=True)
    default = db.Column(db.Boolean, default=False)      # 只有一个角色的字段要设为True,其它都为False
    permissions = db.Column(db.Integer)                 # 不同角色的权限不同
    users = db.relationship('User', backref='itsrole')  # Role对象引用users,User对象引用itsrole
                                                        # 是隐形存在的属性,一对多
    @staticmethod
    def insert_roles():
        roles = {
            'User':(Permission.FOLLOW|Permission.COMMENT|
                     Permission.WRITE_ARTICLES, True),     # 只有普通用户的default为True
            'Moderare':(Permission.FOLLOW|Permission.COMMENT|
                    Permission.WRITE_ARTICLES|Permission.MODERATE_COMMENTS, False),
            'Administrator':(0xff, False)
        }
        for r in roles:
            role = Role.query.filter_by(name=r).first()
            if role is None:
                role = Role(name=r)
            role.permissions = roles[r][0]
            role.default = roles[r][1]
            db.session.add(role)
        db.session.commit()

    @staticmethod
    def seed():
        db.session.add_all(map(lambda r: Role(name=r), ['Guests', 'Administrator']))
        db.session.commit()


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String, nullable=True)
    password = db.Column(db.String, nullable=True)
    email = db.Column(db.String, nullable=True, unique=True)     # 新建一个邮箱字段
    role_id = db.Column(db.Integer, db.ForeignKey('roles.id'))
    password_hash = db.Column(db.String, nullable=True)          # 模型中加入密码散列值
    confirmed = db.Column(db.Boolean, default=False)             # 邮箱令牌是否点击
    name = db.Column(db.String(64))         # 用户信息中的昵称
    location = db.Column(db.String(64))     # 用户地址
    about_me = db.Column(db.Text())         # 用户介绍
    member_since = db.Column(db.DATETIME(), default=datetime.utcnow)    # 注册时间,datetime.utcnow不用带上括号
    last_seen = db.Column(db.DATETIME(), default=datetime.utcnow)       # 上次访问时间
    posts = db.relationship('Post', backref='author', lazy='dynamic')            # 一个用户有多条发表，一对多


    def __init__(self, **kwargs):
        super(User, self).__init__(**kwargs)        # 初始化父类
        if self.itsrole is None:
            if self.email == current_app.config['FLASK_ADMIN']:                  # 邮箱与管理者邮箱相同
                self.itsrole = Role.query.filter_by(permissions=0xff).first()    # 权限为管理者
            else:
                self.itsrole =  Role.query.filter_by(default=True).first()       # 默认用户

    def can(self, permissions):          # 检查用户的权限
        return self.itsrole is not None and \
               (self.itsrole.permissions & permissions) == permissions

    def is_administrator(self):         # 检查是否为管理者
        return self.can(Permission.ADMINISTRATOR)

    def ping(self):
        self.last_seen = datetime.utcnow()         # 刷新上次访问时间
        db.session.add(self)
        db.session.commit()

    def gravatar(self, size=100, default='identicon', rating='g'):
        if request.is_secure:
            url = 'https://secure.gravatar.com/avatar'
        else:
            url = 'http://www.gravatar.com/avatar'
        hash = hashlib.md5(self.email.encode('utf-8')).hexdigest()
        return '{url}/{hash}?s={size}&r={rating}&d={default}'.format(url=url, hash=hash,
                                                            size=size, rating=rating,
                                                            default=default)

    @property             # 试图读取password的值，返回错误, 因为password已经不可能恢复了
    def password(self):
        raise AttributeError('password is not a readable attribute')

    @password.setter      # 设置password属性的值时，赋值函数会调用generate_password_hash函数
    def password(self, password):
        self.password_hash = generate_password_hash(password)

    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)

    def generate_confirm_token(self, expiration=3600):
        s = Serializer(current_app.config['SECRET_KEY'], expires_in=expiration)
        return s.dumps({'confirm': self.id})               # 返回一个token

    def confirm(self, token):
        s = Serializer(current_app.config['SECRET_KEY'])
        try:
            data = s.loads(token)
        except:
            return False
        if data.get('confirm') != self.id:
            return False
        self.confirmed = True
        db.session.add(self)           # 把confirmed字段更新到数据库中，但是还没有提交
        db.session.commit()
        return True

    # 产生虚拟用户
    @staticmethod
    def generate_fake(count=10):
        from sqlalchemy.exc import IntegrityError
        from random import seed
        import forgery_py

        seed()
        for i in range(count):
            u = User(email=forgery_py.internet.email_address(),
                     username=forgery_py.internet.user_name(True),
                     password=forgery_py.lorem_ipsum.word(),
                     confirmed=True,
                     name=forgery_py.name.full_name(),
                     location=forgery_py.address.city(),
                     about_me=forgery_py.lorem_ipsum.sentence(),
                     member_since=forgery_py.date.date(True))
            db.session.add(u)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()

    # @staticmethod
    # def on_create(target, value, oldvalue, initiator):
    #     target.role = Role.query().filter_by(name='Guests').first()

class AnonymousUser(AnonymousUserMixin):   # 匿名用户
    def can(self, permissions):
        return False

    def is_administrator(self):
        return False

class Post(db.Model):
    __tablename__ = 'posts'
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text)
    body_html = db.Column(db.Text)                   # 服务器上的富文本处理字段
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'))

    @staticmethod
    def generate_fake(count=10):
        from random import seed, randint
        import forgery_py

        seed()
        user_count = User.query.count()
        for i in range(count):
            u = User.query.offset(randint(0, user_count-1)).first()
            p = Post(body=forgery_py.lorem_ipsum.sentences(randint(1,3)),
                    timestamp=forgery_py.date.date(True),
                    author=u)
            db.session.add(p)
            db.session.commit()

    @staticmethod
    def on_body_changed(target, value, oldvalue, initiator):
        allow_tags = ['a', 'abbr', 'acronym', 'b', 'blockquote', 'code',
                      'em', 'i', 'li', 'ol', 'pre', 'strong', 'ul',
                      'h1', 'h2', 'h3', 'p']
        target.body_html = bleach.linkify(bleach.clean(markdown(value, output_format='html'),
                                                       tags=allow_tags, strip=True))

@login_manager.user_loader      #加载用户的回调函数,成功后得到当前用户
def load_user(user_id):
    return User.query.get(int(user_id))

login_manager.anonymous_user = AnonymousUser   # 将其设为用户未登陆时的current_user的值

# db.event.listen(User.username, 'set', User.on_create)
db.event.listen(Post.body, 'set', Post.on_body_changed)