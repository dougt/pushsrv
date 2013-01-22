import time
import warnings
from . import StorageBase, StorageException
from .. import logger, LOG, inRecovery
from sqlalchemy import (Column, Index, Integer, String,
                        create_engine, MetaData, text)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker

Base = declarative_base()


class SimplePushSQL(Base):
    __tablename__ = 'simplepush'
    ## this should be a multi-column index. No idea how to do that
    ## cleanly in SQLAlchemy's ORM.
    pk = Column('pk', String(51), primary_key=True, unique=True)
    chid = Column('chid', String(25), index=True)
    uaid = Column('uaid', String(25), index=True)
    vers = Column('version', String(255), nullable=True)
    last = Column('last_accessed', Integer, index=True)
    state = Column('state', Integer, default=1)


class Storage(StorageBase):
    __database__ = 'simplepush'

    def __init__(self, config, **kw):
        try:
            super(Storage, self).__init__(config, **kw)
            self.metadata = MetaData()
            self._connect()
            #TODO: add the most common index.
        except Exception, e:
            logger.log(msg='Could not initialize Storage "%s"' % str(e),
                       type='error', severity=LOG.CRITICAL)
            raise e

    def _connect(self):
        try:
            userpass = ''
            host = ''
            if (self.settings.get('db.user')):
                userpass = '%s:%s@' % (self.settings.get('db.user'),
                                       self.settings.get('db.password'))
            if (self.settings.get('db.host')):
                host = '%s' % self.settings.get('db.host')
            dsn = '%s://%s%s/%s' % (self.settings.get('db.type', 'mysql'),
                                    userpass, host,
                                    self.settings.get('db.db',
                                                      self.__database__))
            self.engine = create_engine(dsn, pool_recycle=3600)
            Base.metadata.create_all(self.engine)
            self.Session = scoped_session(sessionmaker(bind=self.engine,
                                                       expire_on_commit=True))
            #self.metadata.create_all(self.engine)
        except Exception, e:
            logger.log(msg='Could not connect to db "%s"' % repr(e),
                       type='error', severity=LOG.EMERGENCY)
            raise e

    def pk(self, uaid, chid):
        return '%s.%s' % (uaid, chid)

    def health_check(self):
        try:
            healthy = True
            session = self.Session()
            import pdb; pdb.set_trace()
            sp = SimplePushSQL(pk = self.pk('test','test'),
                               chid='test', uaid='test',
                               vers=0, last=int(time.time()))
            session.commit()
            sp = self.session.query(SimplePushSQL).filter_by(chid='test')
            session.delete(sp)
        except Exception, e:
            warnings.warn(str(e))
            return False
        return healthy

    def update_chid(self, chid, vers, logger):
        if chid is None:
            return False
        session = self.Session()
        try:
            rec = session.query(SimplePushSQL).filter_by(chid=chid,
                                                         state=1).first()
            if (rec):
                rec.vers = vers
                rec.last = int(time.time())
                session.commit()
                return True
        except Exception, e:
            logger.warn(str(e))
            raise e
        return False

    def register_chids(self, uaid, pairs, logger):
        try:
            session = self.Session()
            for pair in pairs:
                session.add(SimplePushSQL(pk=self.pk(uaid, pair['channelID']),
                                          chid=pair['channelID'],
                                          uaid=uaid,
                                          vers=pair['version'],
                                          last=int(time.time())))
            session.commit()
        except Exception, e:
            import pdb; pdb.set_trace()
            logger.error(str(e))
            return False
        return True

    def register_chid(self, uaid, chid, logger):
        if chid is None or uaid is None:
            return False
        try:
            self.register_chids(uaid, [{'channelID': chid,
                                        'version': None}], logger)
        except Exception, e:
            import pdb; pdb.set_trace()
            logger.error(str(e))
            return False
        return True

    def delete_chid(self, uaid, chid, logger):
        if chid is None or uaid is None:
            return False
        try:
            session = self.Session()
            rec = session.query(SimplePushSQL).filter_by(pk=self.pk(uaid,
                                                         chid)).first()
            if rec:
                rec.state = 0
                #rec.delete()
                session.commit()
        except Exception, e:
            import pdb; pdb.set_trace();
            logger.error(str(e))
            return False
        return True

    def get_updates(self, uaid, last_accessed=None, logger=None):
        if uaid is None:
            raise StorageException('No UserAgentID provided')
        try:
            sql = ('select chid, version, state from ' +
                   'simplepush where uaid=:uaid')
            params = {'uaid': uaid}
            if last_accessed:
                sql += ' and last_accessed >= :last'
                params['last'] = last_accessed
            records = self.engine.execute(text(sql), **dict(params))
            digest = []
            updates = []
            expired = []
            for record in records:
                if record.state:
                    digest.append(record.chid)
                    updates.append({'channelID': record.chid,
                                    'version': record.version})
                else:
                    expired.append(record.chid)
            if len(updates):
                return {'digest': ','.join(digest),
                        'updates': updates,
                        'expired': expired}
            return None
        except Exception, e:
            import pdb; pdb.set_trace();
            if logger:
                logger.error(str(e))
            raise e
        return False

    def reload_data(self, uaid, data, logger):
        # Only allow if we're in recovery?
        if uaid is None:
            raise StorageException('No UserAgentID specified')
        if data is None or len(data) == 0:
            raise StorageException('No Data specified')
        if self._uaid_is_known(uaid):
            raise StorageException('Already Loaded Data')
        try:
            session = self.Session()
            digest = []
            if session.query(SimplePushSQL).filter_by(uaid=uaid).count():
                return False
            for datum in data:
                chid = datum.get('channelID')
                session.add(SimplePushSQL(pk=self.pk(uaid, chid),
                                          chid=chid,
                                          uaid=uaid,
                                          vers=datum.get('version')))
                digest.append(datum.get('channelID'))
            session.commit()
            return ",".join(digest)
        except Exception, e:
            import pdb; pdb.set_trace();
            logger.error(str(e))
        return False

    def _get_record(self, chid):
        result = []
        try:
            session = self.Session()
            recs = session.query(SimplePushSQL).filter_by(chid=chid)
            for rec in recs:
                result.append(rec.__dict__)
            return result
        except Exception, e:
            import pdb; pdb.set_trace()
            logger.error(str(e))

    def _uaid_is_known(self, uaid):
        return self.Session().query(SimplePushSQL).filter_by(
                uaid=uaid).first() is not None

    def purge(self):
        session = self.Session()
        sql = 'delete from simplepush;'
        self.engine.execute(text(sql))
        session.commit()