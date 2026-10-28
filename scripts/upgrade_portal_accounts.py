"""Apply portal metadata migration and preserve account/password/history data."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def upgrade():
    from auth import _configured_tokens
    from portal_store import PostgresPortalStore
    configured={user.user_id:user for user in _configured_tokens().values()}
    with PostgresPortalStore().transaction() as cursor:
        cursor.execute((ROOT/'db/migrations/005_portal_registration.sql').read_text(encoding='utf-8'))
        cursor.execute("SELECT username,user_id,roles FROM portal_accounts FOR UPDATE")
        accounts=cursor.fetchall()
        administrator = next((row for row in accounts if row['username']=='ys10087'), None)
        if (administrator is None or administrator['user_id'] != 'U-admin'
                or 'U-admin' not in configured):
            raise RuntimeError('管理员必须为本机配置的ys10087/U-admin，停止迁移；未修改账号')
        for row in accounts:
            if row['username']=='ys10087':
                roles=['customer','admin','knowledge_admin']
            elif row['roles'] is not None:
                continue
            else:
                legacy=configured.get(row['user_id'])
                roles=['customer']
                if legacy and 'developer' in legacy.roles:roles.append('developer')
            cursor.execute('UPDATE portal_accounts SET roles=%s WHERE username=%s',(roles,row['username']))
    print('迁移完成；管理员已设置，密码、订单和历史未覆盖。')


if __name__=='__main__':upgrade()
